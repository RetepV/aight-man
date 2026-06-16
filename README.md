# aight-man

This is "aight-man", the **A**rtificial **I**ntelligence **G**erry-rigged **H**allucinated **T**TY pac<b>-man</b>!

It's a pac man clone made for TTY terminals, built on top of curses!

<img width="640" height="932" alt="IMG_4664" src="https://github.com/user-attachments/assets/7c9d3b69-bb29-4812-b9aa-81db4f781b3e" />

It was rigged together by Claude Opus 4.8. Apparently Opus 4.8 was fed with enough information about Pac-Man, probably including some Pac-Man clones that people wrote over the years, to come up with this fully working, and I guess quite faithful, Pac-Man clone **in one go**.

I wanted it for testing my SporosTerm terminal and testing out Claude. No other reason. It was all just for fun, really. And Clause did indeed amaze me with the result.

Do not ask me anything about the code. It is all vibe-coded (really, can you call one prompt "vibe-coded"?). I did not make an attempt at understanding it. Because I don't care, not because I can't understand it. ;)

Note that if you want to run it, you'll have to define a proper curses-supported terminal. I use terminal type 'xterm-16color', which works great on SporosTerm when it's set to ANSI/VT emulation.

Also, here is a small script that I use to manually set up an agetty shell on a serial port:

```
#!/bin/bash
sudo killall agetty 2> /dev/null
AGETTY_PORT="ttyUSB0"
sudo setsid /sbin/agetty -8 -h -J ${AGETTY_PORT} 921600,460800,230400,115200,9600,2400,110 decansi
echo "agetty started on /dev/${AGETTY_PORT}"
ps -ef | grep agetty | grep -v grep
```

This was my prompt:

> Write a pac-man game in Python for an ANSI serial terminal. Be as close to the original Pac-Man arcade game as possible, but use ANSI terminal escape codes for rendering. Render Pac-Man and the ghosts as one character. Use only ASCII character codes. Use the curses module from the Python Standard Library. Make the display size fixed 80 columns by 24 rows. Make the maze as similar to the arcade pac-man as possible, within the constraints of the display. The dots, power pills, pac-man and ghosts can be one character of size. Implement all gameplay details of the original pac-man arcade game.

Claude's reply:

> The game is done and verified: [pacman.py](pacman.py) runs cleanly in a real terminal session (exit 0, maze, dots, Pac-Man, and ghosts all rendering), and its built-in headless self-test passes. Run it with `python3 pacman.py` in any 80×24-or-larger terminal; `python3 pacman.py --selftest` checks the game logic without a terminal.
> 
> **Layout.** The arcade's 28×31 maze is compressed to 28×22 tiles and drawn two screen columns per tile (so the proportions look right), centered in 80×24 with a score line on top and lives/help/fruit on the bottom row. The structure mirrors the original: center ghost house with a door, wrap-around tunnel row passing beside the house, four corner energizers, the top-center divider, and side corridors — 208 dots and pills, all confirmed reachable by flood-fill. Everything is plain ASCII: `##` walls, `.` dots, blinking `o` energizers, `C`/`O` chomping Pac-Man, colored `M` ghosts, `"` for returning eyes.
> 
> **Arcade behavior implemented:**
> - **Ghost AI** — each ghost uses its real targeting: Blinky chases Pac-Man, Pinky aims 4 tiles ahead (including the original up-direction overflow bug that also shifts the target left), Inky doubles the vector from Blinky through a point 2 tiles ahead, and Clyde chases until within 8 tiles then retreats to his corner. Direction choice uses the arcade's up/left/down/right tie-break, ghosts never reverse on their own, and the "red zone" tiles forbid upward turns.
> - **Modes** — per-level scatter/chase schedules with direction reversal on every switch; frightened mode pauses the schedule, ghosts turn blue and move randomly, flash before recovering, and are worth 200/400/800/1600. Frightened duration shrinks by level and disappears at level 17+ (pills then only reverse ghosts).
> - **Ghost house** — per-ghost dot counters (Pinky/Inky/Clyde), the global counter after a death (with the Clyde counter-retirement quirk), and the no-dot timeout release; eaten ghosts return as fast-moving eyes through the door and revive.
> - **Speeds** — the arcade percentage tables by level for Pac-Man, ghosts, frightened, and tunnel slowdown, plus Cruise Elroy stage 1/2 for Blinky (enabled only after Clyde exits). Pac-Man stalls 1 frame per dot and 3 per energizer.
> - **Scoring and flow** — 10/50 point dots/pills, two fruit spawns per level (thresholds and counters scaled from the arcade's 70/170 of 244 dots) with the full cherry-through-key sequence and point values, score popups, extra life at 10,000, 3 lives, READY!/death-animation/GAME OVER sequences, and the white maze flash on level clear.
> 
> Controls are arrow keys or WASD with buffered turns (you can press a direction before the gap, as in the arcade), P to pause, Q to quit, R to restart after game over. The loop runs at the arcade's 60 Hz, and since curses only transmits screen diffs, it stays light enough for a serial line.
