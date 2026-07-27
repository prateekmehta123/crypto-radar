# Start here

Written for someone who doesn't code. Every command is copy-paste — you don't need to
understand them, but I've said what each one does so you're not pasting blind.

**Be honest with yourself about one thing first:** this is real server software. There is
no click-only version. If you follow this exactly it will work, but budget an hour and
expect to paste about fifteen commands.

---

# Part 1 — See it working on your own computer first

Do this before you spend money on AWS. It takes 15 minutes, costs nothing, and if
something is wrong you find out now instead of after setting up a server.

### 1.1 Install Python

Go to **python.org/downloads** and install Python 3.12 or newer.

On Windows, during install, **tick the box that says "Add python.exe to PATH"**. It's easy
to miss and everything fails later without it.

### 1.2 Open a terminal

- **Windows** — press Start, type `powershell`, press Enter.
- **Mac** — press Cmd+Space, type `terminal`, press Enter.

A window with a text prompt appears. This is where every command below goes.

**Check the prompt before you type anything.** There are two different prompts and they
are easy to mix up:

| Prompt | What it is | Correct? |
|---|---|---|
| `PS C:\Users\You>` or `you@Mac ~ %` | the terminal | yes — use this |
| `>>>` | Python | no — you're in the wrong place |

If you see `>>>`, type `exit()` and press Enter to get back out. You land in `>>>` by
typing `python` on its own. You never want that here — every command below is either
`python` **followed by a filename**, or not Python at all.

Symptom if you get this wrong: `cd` gives you a `SyntaxError` mentioning
`unicodeescape`. That's Python trying to read a Windows path as code.

### 1.3 Unzip the code somewhere you can find

Unzip `crypto-radar.zip` to your Desktop. You should end up with a folder called
`crypto-radar` containing `main.py`.

### 1.4 Go to that folder in the terminal

Type `cd ` (with a space after it), then **drag the crypto-radar folder from your Desktop
into the terminal window** — it fills in the path for you. Press Enter.

If the path contains spaces or brackets (a folder like `files (2)` does), wrap it in
double quotes:

```
cd "C:\Users\You\Downloads\files (2)\crypto-radar"
```

Check you're in the right place:

```
dir main.py
```

(On Mac use `ls main.py`.) If it says the file exists, you're in the right folder. If it
says not found, you're in the wrong folder — repeat this step.

### 1.5 Install what it needs

```
pip install -r requirements.txt
```

Takes 2–4 minutes. Lots of scrolling text is normal.

If `pip` isn't recognised on Windows, use `python -m pip install -r requirements.txt`.

### 1.6 Check it works — no internet needed

```
python selftest.py
```

Takes about 2 minutes. You want to see **ALL CHECKS PASSED** at the bottom. This tests
the maths, the backtester, and the web page without touching Binance at all.

### 1.7 Check your internet can reach Binance

```
python preflight.py
```

Look at the **verdict** at the bottom.

- "All checks passed" → good, continue.
- Anything mentioning **451** → your internet connection is in a country Binance blocks.
  Note this, because your AWS server will need to be in Mumbai, Singapore, or Tokyo.

### 1.8 Run it

```
python main.py
```

Then open **http://localhost:8080** in your browser.

The page will say the first scan is running. Give it a minute and rows will appear.

To stop it, click the terminal window and press **Ctrl+C**.

**If you got here, everything works.** Part 2 is only about keeping it running 24/7
without your laptop being on.

---

# Part 2 — Put it on AWS (EC2)

We'll use an EC2 `t4g.small` instance. Its storage (EBS) survives restarts, which matters:
the database it builds up is the one thing in this project that can't be re-downloaded
from anywhere.

You'll use **EC2 Instance Connect**, which opens a terminal inside your browser. No key
files, no PuTTY, nothing to install.

Roughly $12–15/month against your credits.

### 2.1 Put the code on GitHub

The server needs somewhere to download the code from. You don't need to know git for this.

1. Make an account at **github.com**.
2. Click **+** at the top right → **New repository**.
3. Name it `crypto-radar`, choose **Private**, click **Create repository**.
4. Click **uploading an existing file**.
5. Open your unzipped `crypto-radar` folder, select everything inside it, drag it into the
   browser. Wait for uploads to finish, then click **Commit changes**.

You should see `main.py`, a `radar` folder and a `web` folder listed.

Now you need two things:

**The address** — click the green **Code** button, copy the HTTPS URL. Looks like
`https://github.com/yourname/crypto-radar.git`.

**A token** (private repos need one instead of a password) — click your avatar →
**Settings** → scroll down to **Developer settings** → **Personal access tokens** →
**Tokens (classic)** → **Generate new token (classic)**. Tick the **repo** box. Generate,
then **copy it immediately** — GitHub shows it once and never again.

### 2.2 Launch the instance

Go to the **EC2 console** and sign in.

**First, set the region.** Top right corner, click the region name, choose
**Asia Pacific (Mumbai) ap-south-1**.

This is the single most important step on this page. Binance refuses connections from US
servers. If you leave this on a US region, nothing works, and you'll have to start over.

Now click **Launch instance**:

1. **Name** — `radar`

2. **Application and OS Images** — select **Ubuntu**, then in the dropdown pick
   **Ubuntu Server 24.04 LTS**.

   Underneath, change **Architecture** to **64-bit (Arm)**.

   Don't skip this. `t4g` instances are ARM chips. If the architecture says x86 you won't
   be able to select `t4g.small` at all in the next step, and that's the confusing symptom
   of having missed this dropdown.

3. **Instance type** — `t4g.small`

4. **Key pair** — choose **Proceed without a key pair**. You're using the browser terminal,
   so there's no key file to look after.

5. **Network settings** — click **Edit**, then set up two inbound rules:

   **Rule 1 — SSH access for the browser terminal**
   - Type: `ssh`, Port `22`
   - Source type: **Custom**
   - In the source box, start typing `com.amazonaws.ap-south-1.ec2-instance-connect` and
     select the prefix list that appears

   That prefix list is AWS's own address range for the browser terminal. Using it instead
   of "Anywhere" means only AWS's console can SSH in, not the whole internet.

   **Rule 2 — the dashboard**
   - Click **Add security group rule**
   - Type: **Custom TCP**, Port range `8080`
   - Source type: **My IP**

   "My IP" restricts the dashboard to your current internet connection. Worth doing: the
   page shows your positions and price levels.

6. **Configure storage** — change `8` GiB to **20** GiB, type **gp3**.

7. Click **Launch instance**.

Wait about a minute until Instance state shows **Running** and both status checks pass.

### 2.3 Give it a permanent address

By default the public address changes every time the instance stops. Fix that:

1. Left sidebar → **Elastic IPs** → **Allocate Elastic IP address** → **Allocate**.
2. Select it → **Actions** → **Associate Elastic IP address**.
3. Choose your `radar` instance → **Associate**.

Write down the IP. It looks like `13.234.56.78`.

If you later delete the instance, release the Elastic IP too — AWS charges for ones sitting
unused.

### 2.4 Open the browser terminal

Select your instance → **Connect** button at the top → **EC2 Instance Connect** tab →
**Connect**.

A black terminal opens in a new tab. Everything below is pasted there. Paste with
**Ctrl+Shift+V**.

If it fails to connect, the SSH rule from step 2.2 is wrong. Go to your instance →
**Security** tab → click the security group → **Edit inbound rules**, and make sure the
port 22 rule's source is the `com.amazonaws.ap-south-1.ec2-instance-connect` prefix list.

### 2.5 Download the code

Paste this, with your own URL from step 2.1:

```
sudo mkdir -p /opt/radar && sudo chown $USER /opt/radar
git clone https://github.com/yourname/crypto-radar.git /opt/radar
```

It asks for a username (your GitHub username) and a password — **paste the token, not your
GitHub password**. Nothing appears on screen while you paste it. That's normal. Press Enter.

Confirm it worked:

```
ls /opt/radar/main.py
```

### 2.6 Install and start

```
cd /opt/radar && sudo bash deploy/install.sh
```

About 5 minutes. It prints six numbered steps.

If it stops at step 1 with a **451** message, the instance is in a blocked region.
Terminate it and redo step 2.2 with Mumbai selected.

When it finishes it prints a box with your **address, username and password**. Copy the
password now — it is not shown again.

### 2.7 Open it

Browse to `http://YOUR-ELASTIC-IP:8080`, log in as `radar` with that password.

First scan takes about a minute; the page tells you it's working. After that it refreshes
itself every 20 seconds.

It now runs continuously, restarts itself if it crashes, and comes back after a reboot.

# Everyday things

Open the browser terminal (step 2.5) and paste:

| What you want | Command |
|---|---|
| See what it's doing right now | `sudo journalctl -u radar -f` (Ctrl+C to exit) |
| Restart it | `sudo systemctl restart radar` |
| Check it's alive | `sudo systemctl status radar` |
| Change a setting | `sudo nano /etc/radar.env` then restart |

In `nano`, save with **Ctrl+O**, Enter, then exit with **Ctrl+X**.

To update the code after changing it on GitHub:

```
cd /opt/radar && sudo -u radar git pull && sudo systemctl restart radar
```

To stop paying: EC2 → select the instance → **Instance state** → **Terminate**. Then
release the Elastic IP under **Elastic IPs**, or AWS keeps charging for it.

Back up the database occasionally — it holds open-interest history that Binance only keeps
for 30 days and cannot be re-downloaded:

```
sudo sqlite3 /opt/radar/data/radar.db ".backup '/tmp/radar-backup.db'"
```

---

# If something breaks

**The page won't load.** Usually the port 8080 rule — your home IP changed. Google "what is
my ip", then in EC2 go to your instance → **Security** tab → click the security group →
**Edit inbound rules** → update the 8080 rule's source to **My IP** again.

**The browser terminal won't connect.** The SSH rule's source needs to be the
`com.amazonaws.ap-south-1.ec2-instance-connect` prefix list, not your IP.

**"451" anywhere.** The instance is in a Binance-blocked region. The only fix is terminating
it and launching a new one in Mumbai, Singapore or Tokyo.

**Can't select t4g.small when launching.** The AMI architecture is set to x86. Go back to
step 2 and change **Architecture** to **64-bit (Arm)**.

**`SyntaxError` when you type `cd`.** You're at the `>>>` Python prompt instead of the
terminal. Type `exit()`, press Enter, and try again. See the table in step 1.2.

**`pip` is not recognised (Windows).** Use `python -m pip install -r requirements.txt`
instead. If that also fails, Python was installed without ticking "Add python.exe to
PATH" — reinstall it and tick that box.

**The page loads but stays empty.** The first scan hasn't finished. Give it two minutes,
then check `sudo journalctl -u radar -f` for errors.

**A red or yellow bar on the dashboard.** It's telling you what went wrong in plain words.
The scanner keeps showing you the last good result while it retries.

**It stopped working after being fine.** Binance occasionally changes which regions it
blocks. `sudo journalctl -u radar -f` will say if that's what happened.

---

# One thing worth saying

You now have a system that will show you specific entry prices, stop losses and targets,
refreshed every five minutes, on a clean dashboard. That presentation makes the numbers
feel more authoritative than they've earned.

Nothing in it has been tested against real market history yet. The backtester is built and
works, but it hasn't been run on real Binance data — so every number on that dashboard is
currently a well-formatted guess.

Read it for a few weeks without trading it. Write down what you would have done and what
would have happened. That costs you nothing and is the only way to find out whether any of
it works.
