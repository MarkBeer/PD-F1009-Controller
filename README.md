# CD index

A photo of 48 jewel cases on a table, indexed left to right and top to bottom (6 columns, 8 rows). Index 1 is the top-left case.

`cd_index.csv` has one row per track. Isolated cover crops are in `output/crops/` (`01.jpg` through `48.jpg`). The source photo is `assets/a760bb13-1170-47fb-88be-bf50082ed5f3.jpg`.

## How the discs were identified

Each case was cropped from the photo and matched with a reverse image search (Yandex Images), then checked against the artwork itself. Tracklists come from the matching Discogs release, preferring the UK retail CD. Where the UK shop copy was the Europe-wide Warner, Island, EMI, or Polydor pressing, that release is used. A few budget titles were only issued as European retail CDs (Jingle, Arcade, Superior, Music for Pleasure, Weton-Wesgram); those editions are the ones whose covers match the photo.

## Columns

| Column | Meaning |
| --- | --- |
| Input Image | Source photo filename |
| Index | Position in the photo, 1–48, left to right and top to bottom |
| CD Artist | Album artist. Mix compilations are Various |
| Album | Release title |
| CD Number | Disc number in the set. Single discs are 1 |
| Track Artist | Performer of that track. On a single-artist CD this matches CD Artist, plus any featured credit |
| Track Title | Song title |
| Cover Crop | Path to that case’s cropped artwork |

No case was left unidentified, and every track was filled from the matched release.
