# CD index

A photo of jewel cases is cropped into one cover per case, each cover is matched with a reverse image search, and the matching Discogs CD is written as one CSV row per track.

The photo already in this repo is `assets/a760bb13-1170-47fb-88be-bf50082ed5f3.jpg`: 48 cases, 6 columns by 8 rows, index 1 at the top left. Its curated track list is `cd_index.csv`. The same steps are the `cdindex` command, so another photo can be run the same way.

## Run it on a photo

```bash
pip install -r requirements.txt
python -m cdindex run photo.jpg --out output/run
```

That does four steps. Each one can also be run on its own.

1. **detect** finds a regular grid and writes `crops/01.jpg`, `crops/02.jpg`, … plus `preview.jpg` and `grid.json`. Cases are numbered left to right, top to bottom.
2. **search** uploads each crop to Yandex Images and saves the tags and object titles under `yandex/`.
3. **identify** searches Discogs for those titles, prefers a UK retail CD (then UK & Europe, then Europe), and writes `review.json`. Album releases are preferred to CD singles of the same title.
4. **export** writes `cd_index.csv`, one row per track.

Check `preview.jpg` before trusting the search. The detector expects a roughly front-on grid of similar cases. If a row is clipped, run detect again with the count you can see, or edit the boxes in `grid.json` and run detect with `--grid`:

```bash
python -m cdindex detect photo.jpg --out output/run --rows 8 --cols 6
python -m cdindex search --out output/run
python -m cdindex identify --out output/run
python -m cdindex export --out output/run
```

A single cover is `--rows 1 --cols 1`.

Yandex and Discogs have to be reachable. Discogs allows about 25 unauthenticated requests a minute; the default pause is 2.7 seconds. A token in `DISCOGS_TOKEN` raises that limit. Searches and releases already on disk are reused, so a second run does not repeat them unless you pass `--force`.

## When the match is wrong

`review.json` has one entry per case: the queries, the Yandex tags, the chosen release, and the other Discogs hits. `status` is `identified`, `needs_review`, or `unable to identify`.

Two things still need a person. The image search sometimes names a different album with similar art, and several pressings share a title (the 1CD and the 2CD, or two catalogue numbers). The front cover does not always say which one was photographed. Pin the release in `output/run/overrides.json` and run identify and export again:

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
