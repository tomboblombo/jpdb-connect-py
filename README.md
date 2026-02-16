# About This Project

This project (jpdb-connect-py) is essentially the same as the [various](https://github.com/kampffrosch94/jpdb-connect) [other](https://github.com/Dorifor/jpdb-connect-android) JPDB Connect projects that have existed, but my goals in creating it were unique in two ways.
- First, I wanted the project to be easily distributable in the form of executables.
- Second, I wanted to leverage the [undocumented image and audio upload endpoints](https://discord.com/channels/799891866924875786/833939726078967808/1424485929858498581) to make use of tools like [asbplayer](https://github.com/killergerbah/asbplayer) to their full extent.

Given that, I need to say that in it's given state, the project has really only been tested/optimized for the ['Export' function](https://docs.asbplayer.dev/docs/getting-started/mining-subtitles#mine-subtitles) in asbplayer. Any other working functions are incidental.

# Installation
Simply download the relevant executable from the ['releases' tab](https://github.com/TomsJensen/JPDB_Connect_Py/releases) and run it. You'll be prompted for your [JPDB API key](https://jpdb.io/settings) on the first run upon which it will be stored locally for future uses.

# Usage
Just let it run, feel free to minimize it. All other setup is done through other tools, see [asbplayer's setup guide](https://docs.asbplayer.dev/docs/getting-started/mining-subtitles#configure-asbplayer) for more info.

Two things worth noting:
- The application will automatically create a 'JPDB Connect' deck upon responding to it's first requests that require the creation of a card.
- Asbplayer doesn't automatically populate the 'Word' field, this is something you'll have to do manually before exporting, otherwise you'll be met with an error.

Happy mining!
