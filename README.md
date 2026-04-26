# HSRPARSE
-----
Store your SESSDATA GAMEDATA in env.json for use. 
```
    {
        "SESSDATA": "",
        "GAME_DATA_PATH": "~/GameData/turnbasedgamedata/"
    }
```


1. VoiceAtlas
   1. `python cmd/sync_voice.py --name=银狼LV.999` Single Update
   2. `python cmd/sync_voice.py --start_from=银狼LV.999`  Update with reversed-order of character release
   3. `python cmd/sync_voice.py --name=银狼LV.999 --chain-update` Single update with chained character 