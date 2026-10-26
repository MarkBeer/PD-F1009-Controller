# CD index

A photo of jewel cases is cropped into one cover per case, each cover is matched with a reverse image search, and the matching Discogs CD is written as one CSV row per track.

The photo already in this repo is `assets/a760bb13-1170-47fb-88be-bf50082ed5f3.jpg`: 48 cases, 6 columns by 8 rows, index 1 at the top left. Its curated track list is `cd_index.csv`. The same steps are the `cdindex` command, so another photo can be run the same way.

## Run it on a photo

```bash
pip install -r requirements.txt
python -m cdindex align photo.jpg
```

Each photo gets a new folder under `output/`, named with the time and the photo, such as `output/20261026-173500-photo`. A second photo does not reuse the first photo’s searches, crops, or CSV. Passing `--out` with a folder that already belongs to a different photo does the same thing: the old folder stays as it is, and a new one is created beside it. To come back to a photo you already started, pass that photo’s folder:

```bash
python -m cdindex align photo.jpg --out output/20261026-173500-photo
```

The page opens at <http://127.0.0.1:47321>. Use the original photo, not `preview.jpg` (that file already has the green lines drawn on it).

1. Drag a corner to resize. The other three stay where they are, so the top left can stay put while the bottom right moves. Edge handles resize one side. The lines follow the perspective, so nearer cases get larger boxes. Use Zoom to bottom-right when that corner is hard to reach.
2. **Save crops.** That writes `crops/01.jpg`, `crops/02.jpg`, … plus `preview.jpg` and `grid.json`. Cases are numbered left to right, top to bottom.
3. **Search, identify, and export.** The page runs the next three steps and shows what it is doing: which cover is being uploaded, whether Discogs matched it, and a download link when the CSV is ready. The same buttons can run one step at a time.

Those three steps are:

- **search** uploads each crop to Yandex Images and saves the tags and object titles under `yandex/`.
- **identify** searches Discogs for those titles, prefers a UK retail CD (then UK & Europe, then Europe), and writes `review.json`. Album releases are preferred to CD singles of the same title.
- **export** writes `cd_index.csv`, one row per track.

A saved image search is reused only when it is newer than that crop and it actually returned tags. After you realign and save, the replaced crops are uploaded again. Tick “Redo covers that were already searched” to upload every crop again.

To run the whole pipeline in the terminal instead of the page, this also creates a new folder:

```bash
python -m cdindex run photo.jpg
```

`search`, `identify`, and `export` still take the exact run folder:

```bash
python -m cdindex search --out output/20261026-173500-photo
python -m cdindex identify --out output/20261026-173500-photo
python -m cdindex export --out output/20261026-173500-photo
```

A single cover is `--rows 1 --cols 1` on `align` or `run`.

Yandex and Discogs have to be reachable. Discogs allows about 25 unauthenticated requests a minute; the default pause is 2.7 seconds. A token in `DISCOGS_TOKEN` raises that limit. A Discogs search already on disk is reused when the query is the same. `--force` repeats those as well.

## When the match is wrong

`review.json` has one entry per case: the queries, the Yandex tags, the chosen release, and the other Discogs hits. `status` is `identified`, `needs_review`, or `unable to identify`.

Two things still need a person. The image search sometimes names a different album with similar art, and several pressings share a title (the 1CD and the 2CD, or two catalogue numbers). The front cover does not always say which one was photographed. Pin the release in the run folder’s `overrides.json` and run identify and export again:

```json
{
  "12": {"release_id": 711192, "album": "Like a Prayer"},
  "30": {"release_id": 1930797}
}
```

`release_id` is the Discogs release. `album` replaces the title in the CSV only. `query` replaces the phrase sent to Discogs when the cover search was no help.

A case with no usable match is one CSV row whose track title is `unable to identify`. A release with no tracks is `track info unavailable`.

## What each CSV column is

| Column | Meaning |
| --- | --- |
| Input Image | Source photo filename |
| Index | Position in the photo, left to right and top to bottom, starting at 1 |
| CD Artist | Album artist. Mix compilations credited to Various stay Various |
| Album | Release title |
| CD Number | Disc in the set. A single disc is 1 |
| Track Artist | Performer of that track. On a single-artist CD this is the album artist, plus a featured credit when the release lists one |
| Track Title | Song title |
| Cover Crop | Path to that case’s cropped artwork |

Headings such as “Bonus Tracks” are not rows. A silent spacer track is not a row. A position like `2-1` is disc 2. A position like `14.1` on an otherwise single disc stays on disc 1.

## This collection

The measured grid for the 48-case photo, including the slight perspective slope used for the published crops, is `examples/table-48-grid.json`. The Discogs release chosen for each case is `examples/table-48.json`. Rebuild the track table from those saved releases without calling the network:

```bash
python -m cdindex detect assets/a760bb13-1170-47fb-88be-bf50082ed5f3.jpg \
  --grid examples/table-48-grid.json \
  --out output/rerun

python -m cdindex export \
  --selection examples/table-48.json \
  --releases output/discogs/releases \
  --csv output/rerun/cd_index.csv
```

`cd_index.csv` in the repo root is the curated copy. A few titles there follow the sleeve where Discogs spelled them differently (Madame George, Quiet Moments, Ecstasy, The Back Room). The export above keeps the Discogs spelling, and it uses the Discogs title except index 17, whose sleeve title is set to Collection (2CD) in the selection file.

The saved cover crops are `output/crops/01.jpg` through `output/crops/48.jpg`. Yandex responses are under `output/yandex/`. Discogs search hits and release tracklists are under `output/discogs/`.

Tracklists prefer the UK retail CD. Where the UK shop copy is the Europe-wide Warner, Island, EMI, or Polydor pressing, that release is used. A few budget titles were only sold as European retail CDs (Jingle, Arcade, Superior, Music for Pleasure, Weton-Wesgram); those are the editions whose covers match the photo.
