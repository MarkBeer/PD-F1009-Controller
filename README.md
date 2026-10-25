# CD index

A photo of 48 jewel cases on a table, indexed left to right and top to bottom (6 columns, 8 rows). Index 1 is the top-left case.

`cd_index.csv` lists each case with the artist, album, and full tracklist. Isolated cover crops are in `output/crops/` (`01.jpg` through `48.jpg`).

## How the discs were identified

Each case was cropped from the photo and matched with a reverse image search (Yandex Images), then checked against the artwork itself. Tracklists come from the matching Discogs release, preferring the UK retail CD. Where the UK shop copy was the Europe-wide Warner, Island, EMI, or Polydor pressing, that release is used. A few budget titles were only issued as European retail CDs (Jingle, Arcade, Superior, Music for Pleasure, Weton-Wesgram); those editions are the ones whose covers match the photo.

## Columns

| Column | Meaning |
| --- | --- |
| Index | Position in the photo, 1–48 |
| Artist | Album artist. Compilations with many performers are Various Artists; individual track artists are in the tracklist |
| Album | Release title |
| Tracklist | Numbered tracks. Multi-disc sets are marked CD1, CD2, CD3. On compilations, the performer follows the title |

No case was left unidentified, and every tracklist was filled from the matched release.
