from http.server import BaseHTTPRequestHandler, HTTPServer
from PIL import Image
import base64
import io
import json
import os
import requests
import subprocess
import sys
import tempfile
import traceback

API_KEY_FILE = 'jpdb_api_key.txt'
JPDB_BASE = 'https://jpdb.io/api/v1'
PORT = 8765

class AnkiConnectHandler(BaseHTTPRequestHandler):
    jpdb_api_key = None
    jpdb_decks = None
    
    stored_audio = None
    stored_image = None

    # -------------------------
    # Headers / CORS
    # -------------------------
    def set_headers(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_OPTIONS(self):
        self.set_headers()

    # -------------------------
    # POST handler
    # -------------------------
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        raw_body = self.rfile.read(content_length)

        print(f'\n[RAW REQUEST BODY] {raw_body.decode("utf-8", errors="replace"):.1024}...')

        try:
            data = json.loads(raw_body)

            action = data.get('action')
            version = data.get('version')
            params = data.get('params', {})

            # print(f'[REQUEST] action={action} version={version} params={params:.64}...')

            result = self.handle_action(action, params)

            if version <= 2:
                response = result
            else:
                response = {
                    "result": result
                }

        except Exception as e:
            print(f'[ERROR] {e}')
            traceback.print_exc()
            response = {
                "error": str(e)
            }

        self.set_headers()
        response_bytes = json.dumps(
            response,
            ensure_ascii=False,
            separators=(',', ':'),
            allow_nan=False
        ).encode('utf-8')

        print(f'[RESPONSE] {response_bytes.decode("utf-8")}')
        self.wfile.write(response_bytes)

    # -------------------------
    # Action dispatcher
    # -------------------------
    def handle_action(self, action, params):
        match action:
            case "version":
                return 6
            
            case "requestPermission":
                return {
                    "permission": "granted",
                    "requireApiKey": False,
                    "version": 6
                }
            
            case "deckNames":
                return ["JPDB Connect"]

            case "modelNames":
                return ["JPDB Connect"]
            
            case "modelFieldNames":
                return ["Word", "Sentence", "Audio", "Image"]
            
            case "canAddNotes":
                return [True for _ in params['notes']]
            
            case "canAddNotesWithErrorDetail":
                return [{ "canAdd": True } for _ in params['notes']]
            
            case "addNote":
                return self.handle_add_note(params)

            case "storeMediaFile":
                return self.handle_store_media_file(params)
            
            case "multi":
                return self.handle_multi(params)

            case _:
                raise Exception(f"Unsupported action: {action}")

    # -------------------------
    # multi support
    # -------------------------
    def handle_multi(self, actions):
        """
        actions should be a list of:
        [
            {"action": "...", "params": {...}, "version": 6},
            ...
        ]
        """

        results = []

        for item in actions:
            action = item.get("action")
            params = item.get("params", {})
            version = item.get("version")

            try:
                result = self.handle_action(action, params)

                # Versioned responses return wrapped object
                if version == 6:
                    results.append({"result": result, "error": None})
                else:
                    results.append(result)

            except Exception:
                error_obj = {"result": None, "error": "unsupported action"}
                results.append(error_obj)

        return results
    
    def handle_add_note(self, params):
        """
        Translates an Anki-style note into JPDB API calls.
        """

        note = params.get("note")
        if not note:
            raise Exception("Missing note")

        deck_name = note.get("deckName")
        model_name = note.get("modelName")
        fields = note.get("fields", {})
        word = fields.get("Word")
        sentence = fields.get("Sentence")
        audio = note.get("audio") or AnkiConnectHandler.stored_audio
        image = note.get("picture") or AnkiConnectHandler.stored_image

        print(f"Adding note:")
        print(f"Deck={deck_name}")
        print(f"Model={model_name}")
        print(f"Word={word}")
        print(f"Sentence={sentence}")
        print(f"Audio={audio['filename']}")
        print(f"Image={image['filename']}")

        # -----------------------------
        # JPDB PIPELINE
        # -----------------------------
        
        deck_id = self.jpdb_ensure_deck_exists(deck_name)
        print(f"[JPDB PIPELINE] Using deck id={deck_id}")

        print(f"[JPDB PIPELINE] Parsing word: {word}")
        vid, sid, rid = self.jpdb_parse_text(word)
        
        print(f"[JPDB PIPELINE] Adding vocabulary vid={vid} sid={sid} to deck id={deck_id}")
        response = self.jpdb_add_vocabulary(deck_id, vid, sid)
        
        print(f"[JPDB PIPELINE] Setting sentence for vid={vid} sid={sid} to: {sentence}")
        response = self.jpdb_set_card_sentence(vid, sid, sentence)
        
        image_data = base64.b64decode(image['data'])
        avif_bytes = self.convert_image_to_avif_bytes(image_data)
        print (f"[JPDB PIPELINE] Converted image to AVIF ({len(avif_bytes)} bytes)")

        print(f"[JPDB PIPELINE] Uploading image for vid={vid} sid={sid}")
        response = self.jpdb_set_card_image(vid, sid, avif_bytes)

        audio_data = base64.b64decode(audio['data'])
        opus_bytes = self.convert_audio_to_opus(audio_data)
        print(f"[JPDB PIPELINE] Converted audio to Opus ({len(opus_bytes)} bytes)")
        
        print(f"[JPDB PIPELINE] Uploading audio for vid={vid} sid={sid}")
        response = self.jpdb_set_card_sentence_audio(vid, sid, opus_bytes)

        # AnkiConnect expects null result on success
        return None
    
    def handle_store_media_file(self, params):
        root, extension = os.path.splitext(params['filename'])
        match extension.lower():
            case ".jpg" | ".jpeg" | ".png":
                AnkiConnectHandler.stored_image = params
            case ".mp3" | ".wav" | ".ogg":
                AnkiConnectHandler.stored_audio = params

        return None


    def jpdb_post(self, endpoint, payload, files=None):
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {AnkiConnectHandler.jpdb_api_key}"
        }

        response = requests.post(
            f"{JPDB_BASE}/{endpoint}",
            headers=headers,
            json=payload,
            files=files
        )

        if response.ok:
            print(f"[JPDB] Response {response.status_code} {response.text}")            
        else:
            raise Exception(f"[JPDB ERROR] Response {response.status_code}: {response.text}")

        return response.json()
    
    def jpdb_ensure_decks_loaded(self):
        if self.jpdb_decks is not None:
            return  # Already loaded
        
        print(f"[JPDB] Fetching user decks...")
        
        data = self.jpdb_post(
            "list-user-decks",
            {
                "fields": ["name", "id"]
            }
        )

        decks = data.get("decks", [])

        # Convert [[name, id], ...] → {name: id}
        self.jpdb_decks = {name: deck_id for name, deck_id in decks}

        print(f"[JPDB] Loaded {len(self.jpdb_decks)} decks.")
        
    def jpdb_ensure_deck_exists(self, name):
        self.jpdb_ensure_decks_loaded()
        
        if name in self.jpdb_decks:
            return self.jpdb_decks[name]

        return self.jpdb_create_deck(name)
        
    def jpdb_create_deck(self, name):
        data = self.jpdb_post(
            "deck/create-empty",
            {
                "name": name,
                "position": 0
            }
        )

        deck_id = data.get("id")

        if not deck_id:
            raise Exception("Failed to create deck")

        self.jpdb_decks[name] = deck_id

        print(f"[JPDB] Created deck '{name}' (id={deck_id})")

        return deck_id
    
    def jpdb_parse_text(self, text):
        """
        Calls JPDB parse endpoint and returns (vid, sid, rid).
        Assumes exactly one vocabulary result.
        """

        payload = {
            "text": text,
            "token_fields": [],
            "position_length_encoding": "utf16",
            "vocabulary_fields": ["vid", "sid", "rid"]
        }

        data = self.jpdb_post("parse", payload)

        vocab = data.get("vocabulary", [])

        if not vocab:
            raise Exception("No vocabulary returned from JPDB parse")

        if len(vocab) != 1:
            print(f"[WARNING] Multiple vocabulary entries returned: {vocab}")

        vid, sid, rid = vocab[0]

        print(f"[JPDB PARSE] vid={vid}, sid={sid}, rid={rid}")

        return vid, sid, rid
    
    def jpdb_add_vocabulary(self, deck_id, vid, sid):
        return self.jpdb_post(
            "deck/add-vocabulary",
            {
                "id": deck_id,
                "vocabulary": [ [vid, sid] ]
            }
        )
    
    def jpdb_set_card_sentence(self, vid, sid, sentence):
        return self.jpdb_post(
            "set-card-sentence",
            {
                "vid": vid,
                "sid": sid,
                "sentence": sentence
            }
        )
    
    def encode_bytes_for_jpdb(file_bytes):
        return base64.b64encode(file_bytes).decode("ascii")
    
    def convert_image_to_avif_bytes(self, image_data, max_size_kb=50):
        input_buffer = io.BytesIO(image_data)
        img = Image.open(input_buffer)

        output_buffer = io.BytesIO()

        # Start high quality and adjust if needed
        img.save(output_buffer, format="AVIF", quality=50)

        avif_bytes = output_buffer.getvalue()

        if len(avif_bytes) > max_size_kb * 1024:
            print("[WARNING] AVIF exceeds size limit")

        return avif_bytes
    
    def jpdb_set_card_image(self, vid, sid, image_bytes):
        encoded = base64.b64encode(image_bytes).decode("ascii")

        return self.jpdb_post(
            "set-card-image",
            {
                "vid": vid,
                "sid": sid,
                "image": encoded
            }
        )
        
    def convert_audio_to_opus(self, audio_data, max_kb=50):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".input") as temp_in:
            temp_in.write(audio_data)
            temp_in_path = temp_in.name

        temp_out_path = temp_in_path + ".ogg"

        try:
            # Encode to opus, aggressively small
            cmd = [
                get_ffmpeg_path(),
                "-y",
                "-i", temp_in_path,
                "-c:a", "libopus",
                "-b:a", "32k",          # adjust if needed
                "-vbr", "on",
                "-compression_level", "10",
                temp_out_path
            ]

            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            with open(temp_out_path, "rb") as f:
                opus_bytes = f.read()

            if len(opus_bytes) > max_kb * 1024:
                print(f"[WARNING] Audio is {len(opus_bytes)} bytes (>{max_kb}KB)")

            return opus_bytes

        finally:
            if os.path.exists(temp_in_path):
                os.remove(temp_in_path)
            if os.path.exists(temp_out_path):
                os.remove(temp_out_path)

    def jpdb_set_card_sentence_audio(self, vid, sid, audio_bytes):
        encoded = base64.b64encode(audio_bytes).decode("ascii")

        return self.jpdb_post(
            "set-card-sentence-audio",
            {
                "vid": vid,
                "sid": sid,
                "audio": encoded
            }
        )

def load_or_prompt_api_key():
    if os.path.exists(API_KEY_FILE):
        with open(API_KEY_FILE, "r", encoding="utf-8") as f:
            key = f.read().strip()
            if key:
                print("Loaded JPDB API key from file.")
                return key

    # If not found or empty, prompt user
    key = input("Enter your JPDB API key: ").strip()

    with open(API_KEY_FILE, "w", encoding="utf-8") as f:
        f.write(key)

    print("API key saved for future sessions.")
    return key

def get_ffmpeg_path():
    exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"

    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
        path = os.path.join(base_path, exe_name)
        if os.path.exists(path):
            return path
    else:
        base_path = os.path.dirname(__file__)
        path = os.path.join(base_path, exe_name)
        if os.path.exists(path):
            return path

    # fallback to system
    return exe_name

# -------------------------
# Start server
# -------------------------
if __name__ == '__main__':
    print(f'JPDB Connect server running at http://127.0.0.1:{PORT}')
    AnkiConnectHandler.jpdb_api_key = load_or_prompt_api_key()
    HTTPServer(('127.0.0.1', PORT), AnkiConnectHandler).serve_forever()
