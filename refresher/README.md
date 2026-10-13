# Job Buddy Refresher: set-up guide

Hi! This guide is for setting up the **Job Buddy Refresher** on the always-on computer at home (the Optiplex). It takes about **10 minutes** and you only do it once.

## What this is, in plain words

**Job Buddy** is Chandan's job-hunting website. Its job list is only useful if somebody keeps it fresh. This little program does that job:

- Every morning (at **7:30**, unless Chandan changes it) it looks for new jobs on Indeed, LinkedIn and Glassdoor.
- It checks whether old adverts have expired.
- It sends the results to the Job Buddy website.
- It tells the website how it's doing, so Chandan can see it on the website and press **Run now** if he wants a refresh right away.

You don't have to do anything after the set-up. It starts by itself when the computer starts, and it keeps going if the internet drops for a while.

**It is safe:** it only ever *calls out* to the website, so nothing at home is opened up to the internet. It never touches your files or anything outside its own folder.

---

## What you need

- The Optiplex with **Linux** (Ubuntu, Debian, Mint or similar), switched on and online.
- About **10 minutes**, and the computer's password (it's asked for once).
- A **connection code** from Chandan. It looks like a long line of letters and numbers starting with `jbr1.`

Chandan gets the code from the Job Buddy website: **Admin → Refresher → Add a refresher**. It's only shown once, so he'll send it to you directly. Treat it like a password: don't post it anywhere.

---

## Step by step

### 1. Download the program
1. On the computer, open a web browser and go to the Job Buddy page on GitHub: **https://github.com/cchandan/Job-Buddy**
2. Click the green **Code** button, then **Download ZIP**.
   (If you see a login screen, sign in with the GitHub account Chandan shared the project with.)

### 2. Unzip it
Open the **Terminal** (press `Ctrl + Alt + T`) and type these two lines, pressing Enter after each:

```bash
cd ~/Downloads
unzip Job-Buddy-*.zip
```

If it says `unzip: command not found`, run `sudo apt install unzip` first, then try again.

### 3. Run the installer
Still in the Terminal, type:

```bash
cd Job-Buddy-*
bash refresher/install.sh
```

Then:

- If it says Python is missing, it will print the exact command to fix it (usually `sudo apt install python3 python3-venv python3-pip`). Run that, then run `bash refresher/install.sh` again.
- Wait while it installs the tools. This takes **a minute or two**.
- When it asks, **paste the connection code** from Chandan (right-click → Paste, or `Ctrl + Shift + V`) and press Enter. You should see **"Connected to … as …"**.
- At the end it asks for **the computer's password once**. This lets the refresher keep running after a restart even when nobody is logged in. Type it and press Enter (you won't see the letters as you type: that's normal).

When you see **"All done."**, it's set up.

### 4. Check it works
Open a **new** Terminal window and type:

```bash
jbr status
```

You should see that the service is **active (running)** and that the website is connected. Then tell Chandan: on the Job Buddy website under **Admin → Refresher**, the computer should show **Online**. He can press **Run now** to try it.

---

## Everyday use (you probably won't need any of this)

Open a Terminal and type:

| What you want | Type |
|---|---|
| See how it's doing | `jbr status` |
| Watch what it's doing right now (press `Ctrl + C` to stop watching) | `jbr logs` |
| Start a refresh right now and watch it | `jbr refresh` |
| Restart it | `jbr restart` |
| Switch it off for a while | `jbr stop` (and `jbr start` to switch it back on) |

The **time of the morning refresh**, the **job searches** and the **sources** (Indeed, LinkedIn, Glassdoor) are all changed on the Job Buddy website by Chandan. You never change them here.

---

## If something goes wrong

**"jbr: command not found"**: open a *new* Terminal window (the old one doesn't know about it yet). Still not found? Type `~/job-buddy-refresher/jbr status` instead.

**The website shows "Offline" or "Not heard from for a while"**:
1. Is the computer on and connected to the internet?
2. Type `jbr status`. If it doesn't say *running*, type `jbr restart`.
3. Type `jbr logs` and look at the last few lines. Send Chandan a photo of them if you're not sure.

**"the website doesn't recognise this computer any more"**: Chandan removed it on the website. Ask him for a fresh connection code, then run `bash refresher/install.sh` again and paste it.

**It says some job sites "failed" or "rate limited"**: that's normal now and then (the job sites sometimes block). It skips that site, carries on with the others, and tries again next time. Nothing to fix.

**The installer says it couldn't download the tools**: the internet was probably down. Check the connection and run the installer again. It's safe to run it as many times as you like.

---

## Updating to a newer version

When Chandan says there's an update (the website will show **"Out of date"** next to this computer):

1. Download the new ZIP and unzip it, exactly as in steps 1 and 2.
2. Run `bash refresher/install.sh` again from inside the new folder.
   It keeps the connection and the data, so you **don't** need a new code.

---

## Removing it

```bash
jbr uninstall
```

It asks before it removes anything. This only removes the refresher from this computer. The Job Buddy website and its jobs are not affected.

---

## For the curious

- Everything lives in one folder: `~/job-buddy-refresher`. Its small database is in `data/`, and its log is `logs/refresher.log`.
- It runs as a normal user service called `job-buddy-refresher` (`systemctl --user status job-buddy-refresher`).
- It checks in with the website about once a minute. A failed morning refresh is retried up to 3 times, an hour apart. If the computer was off at 7:30, it refreshes as soon as it's back on.
- It uses the free "keyword" tagging, so it needs no AI account.
