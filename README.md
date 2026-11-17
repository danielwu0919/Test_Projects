# Lewis's An-225 Adventure ✈️

A side-scrolling flying game starring the Antonov An-225 "Mriya", the world's
biggest cargo plane. Take off, catch cargo crates, dodge everything else, and
see how far you can fly.

## Play now

**▶ [Play in your browser](https://danielwu0919.github.io/Test_Projects/)**

https://danielwu0919.github.io/Test_Projects/

Nothing to install. It works on computers, tablets and phones.

## How to play

1. Press **SPACE** (or tap the screen) to start the take-off roll.
2. Hold climb to lift off the runway, then steer up and down through the sky.
3. Catch the things with a **green ring** ("CATCH!") and avoid the things with
   a **red warning sign** ("DODGE!").
4. You have three hearts. Lose them all and the flight is over.

### Controls

| Action  | Keyboard                    | Phone / tablet / mouse              |
| ------- | --------------------------- | ----------------------------------- |
| Take off | SPACE                      | Tap anywhere                        |
| Climb   | Hold SPACE, ↑ or W          | Hold the top part of the screen     |
| Dive    | Hold ↓ or S                 | Hold the bottom part of the screen  |
| Pause   | P or Esc                    | —                                   |
| Restart | R or Enter                  | Tap after the game is over          |

The An-225 is a heavy plane: it climbs and sinks slowly, so start your moves
early. A quick tap gives a small hop; holding gives a full climb.

### Catch these

| Item        | What it does                                                         |
| ----------- | -------------------------------------------------------------------- |
| Cargo crate | 50 points. Catch crates in a row to build a combo, up to x5 (250 points). Missing a crate lowers the combo. |
| Repair      | Gives one heart back (or 100 points if you already have all three).  |
| Shield      | Blocks one hit. Lasts about 8 seconds.                               |
| Magnet      | Pulls nearby crates and power-ups towards the plane for about 8 seconds. |

### Dodge these

Each one costs a heart.

| Obstacle     | Appears from | Watch out for                                   |
| ------------ | ------------ | ----------------------------------------------- |
| Birds        | Level 1      | Single birds at first, then V-shaped flocks.    |
| Balloons     | Level 2      | They drift slowly up or down.                   |
| Storm clouds | Level 3      | The cloud glows before it fires lightning below it. |
| Jets         | Level 4      | Fast. A warning arrow shows where one is coming. |

### Tips

- Flying close past an obstacle without touching it earns a **Close call! +25**.
- You can land back on the ground. A gentle landing is free; a hard one costs
  a heart. Distance and score only count while you are in the air.
- A new level starts every 20 seconds. Each level is faster with more
  obstacles, and the sky moves from day to sunset to night.
- Your best score is saved in your browser.

## Run it on your own computer

**Browser version** — download or clone this repository and open `index.html`
in any web browser. Keep the `assets` folder next to it so the game can load
its pictures (without them it falls back to simpler drawn artwork).

**Python version** — a tkinter edition of the same game:

```bash
pip install pillow
python3 lewis_an225_game.py
```

Pillow is optional; it lets the game show the 3D-rendered pictures.

## What is in this repository

| Path                   | Contents                                                        |
| ---------------------- | --------------------------------------------------------------- |
| `index.html`           | The game (the version hosted on GitHub Pages).                  |
| `lewis_an225_game.py`  | Python (tkinter) version of the same game.                      |
| `assets/sprites/`      | The pictures the game draws, rendered from the 3D models.       |
| `assets/blender/`      | The Blender scene and the script that builds and renders it.    |
| `summary.txt`          | Notes on how the project was built.                             |
