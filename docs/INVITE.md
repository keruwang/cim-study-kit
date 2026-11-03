# Inviting a participant

## Before sending anything

1. Decide the participant code (P01, P02, ...), the task family (A-E) and whether the side-task reminder is on. Write it in your assignment sheet.
2. Make sure the upload folder in `study_config.json` is shared so that anyone with the link can add files (Drive: Share, "Anyone with the link", Editor; or a Dropbox file request), and that the repo is reachable for them (public, or they are a collaborator).
3. Send the message below, with the code and family filled in, and `CONSENT.md` attached or linked.

## Message to send (edit the brackets)

Subject: One-hour computer work session for our study

Hi [name],

Thanks for agreeing to take part. The session is one hour on your own computer, doing a real task of yours while thinking aloud, with a small program recording the screen, your clicks and typing, and your voice. You review the recording before anything leaves your machine, and you can delete parts of it. Full details are in the consent text attached; please read it first.

Your participant code is **[P07]** and your task family is **[C: Plan something open]** (the program explains it). Please pick something you actually need to do.

Setup, ideally a day before (10 minutes, one-time):

1. Install Python 3.11 or newer from https://www.python.org/downloads/ and Node.js from https://nodejs.org (both "download and run the installer"). On a Mac, also run `brew install ffmpeg` if you have Homebrew; it lets the program notice when you have gone quiet.
2. Open Terminal (Mac) or PowerShell (Windows) and run:

    git clone https://github.com/keruwang/cim-study-kit.git
    cd cim-study-kit
    python3 study.py setup

   (No git? Download https://github.com/keruwang/cim-study-kit/archive/refs/heads/main.zip, unzip it, and `cd` into the folder instead.)

3. On a Mac, the setup asks for three permissions for your terminal app: Screen & System Audio Recording, Microphone, Accessibility. Allow them, quit Terminal completely (Cmd+Q), reopen it, and run `python3 study.py setup` again until it says "Setup OK".

On the day:

    cd cim-study-kit
    python3 study.py run --participant [P07] --family [C]

A small window walks you through it: consent, your task, a short explanation, then it shrinks to a timer in the corner while you work. Just keep saying what you are thinking as you go. At the end you write a short summary, see what was recorded, delete anything you want, and the program packs a zip and opens the upload page. Drag the zip there and reply to this email with the file name.

If anything goes wrong, stop and message me; nothing is uploaded automatically.

Thanks again,
[your name]

## After the zip arrives

See `docs/RESEARCHER.md`: unzip, transcribe, export the histories, and log the session in the assignment sheet.
