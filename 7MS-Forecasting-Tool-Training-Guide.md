# 7MS Forecasting Tool — Training Guide

**For:** anyone given an account on the forecasting tool
**Application:** https://btiuiztr3zzmfnxhnhbibj.streamlit.app
**Last updated:** 20 August 2026

This guide teaches you how to use the tool day to day. You do not need any technical knowledge. If you want to know how the calculations work under the bonnet, read the companion *Application Documentation* instead.

---

## Part 1 — Getting in for the first time

### Step 1: Open the application

Go to the address above. If the page takes a few seconds and shows a message about waking up, that is normal — the app sleeps when nobody is using it.

### Step 2: Sign in

Enter the username or email address you were given, and the temporary password.

### Step 3: Choose your own password

The tool will insist on this before it lets you go anywhere. You cannot skip it.

- Minimum 10 characters
- Use something you do not use anywhere else
- Write it down somewhere safe, or better, use a password manager

### Step 4: Set up two-step sign-in

You will be asked to do this. It means that knowing your password is not enough to get into the app — you also need your phone. Given what this tool contains, that is worth two minutes.

1. Install an authenticator app on your phone if you do not have one. **Google Authenticator**, **Microsoft Authenticator** and **Authy** all work, all are free, any of them is fine.
2. In the tool, go to **My Account** and click **Start setting it up**.
3. Scan the square QR code with the authenticator app.
4. Your phone will start showing a six-digit number that changes every 30 seconds. Type the current one into the box and submit.

Nothing is saved until that code matches, so you cannot lock yourself out during setup. If it fails, just try again with a fresh code.

### Step 5: Save your recovery codes — do not skip this

The moment two-step is switched on, the tool shows you **eight recovery codes** and offers a download button.

**These are how you get back in if you lose your phone.** They are shown once and never again. Nobody can look them up for you afterwards — not even an administrator, because they are stored scrambled.

Do this now:

- Click the download button, and
- Print them or write them down, and
- Put them somewhere that is **not your phone** — a wallet, a safe, a locked drawer, or a password manager

If your phone and your codes are in the same place, you have no backup.

---

## Part 2 — Signing in from then on

Every time:

1. Username and password.
2. The six-digit code from your authenticator app.

The code changes every 30 seconds. If it is about to change, wait for the next one rather than rushing.

Each code works **once**. If you mistype and then retype the same code, it will be refused — wait for a fresh one.

---

## Part 3 — When something goes wrong with your sign-in

### I lost my phone but I have my recovery codes

At the code screen, enter one of your recovery codes instead of a six-digit code. It works once and is then used up.

Once you are in, go to **My Account**, set up two-step again on your new phone, and issue a fresh set of recovery codes.

### I forgot my password

On the sign-in screen, open **Forgot my password**. You will need:

- Your username
- **Two different recovery codes**
- Your new password, twice

Both codes get used up. You have eight, so this works four times over.

It asks for two rather than one on purpose: one code already gets you past the phone step, so letting one code also reset the password would make a single lost slip of paper enough to take over the account.

After the reset you go back to the sign-in screen and **still need your phone or another recovery code**. The reset changes your password, nothing more.

### I lost my phone AND my recovery codes

Contact Frank. An administrator can switch two-step off for your account so you can get in with your password alone, then you set it up again.

They will ask you to confirm who you are, and they should — a phone call asking for two-step to be turned off is exactly how accounts get stolen.

### I am running low on recovery codes

The tool warns you at three or fewer. Go to **My Account** and issue a fresh set of eight. The old ones stop working immediately.

---

## Part 4 — Finding your way around

The menu is down the left-hand side. What you see depends on your access level, shown in the sidebar.

| Page | What it is for |
|---|---|
| **Dashboard** | The summary. Where cash stands, and when it gets tight |
| **Forecast** | The full day-by-day and month-by-month projection |
| **Payroll** | Import and analyse a payroll period |
| **Terminations** | Work out a liquidación and get it into the forecast |
| **Sage Actuals** | Import figures from Sage 50 |
| **Cash Flow** | Where the assumptions live (administrators only) |
| **Daily Log** | Record what actually happened today |
| **AI Assistant** | Ask questions in plain language |
| **My Account** | Your password and your two-step settings |
| **Accounts** | Manage people (administrators only) |

### Access levels

| Level | What you can do |
|---|---|
| **Viewer** | Read everything. Change nothing |
| **User** | Record actuals, payroll, terminations and Sage figures. Cannot change forecast assumptions |
| **Admin** | Everything, including assumptions and accounts |

If a button is greyed out, your level does not allow it. That is not a fault.

**One rule above all others:** the Dashboard and Forecast are read-only on purpose. You change the numbers on the page that owns them, and the Dashboard follows. Never try to "fix" the Dashboard.

---

## Part 5 — The daily routine (five minutes)

This is the habit that makes the whole tool worth having. A forecast nobody checks against reality is just a guess.

Go to **Daily Log**:

1. **Log what happened.** Money that came in, money that went out. One line each, with the date, the amount, and which category it belongs to.
2. **Enter today's bank balance.** The real one, from the bank.
3. Save.

That is it.

### Why the bank balance matters so much

Once the tool has real balances on two or more dates it can compare what the log says happened against what the bank says happened. When those two disagree, something is missing — an unrecorded payment, a duplicate, a transfer nobody logged. The **Log versus bank** tab on the AI Assistant page will then tell you where the gap probably is.

Without the bank balance, that check cannot run at all.

---

## Part 6 — The payroll routine (twice a month)

After each planilla is processed:

1. Go to **Payroll**.
2. Upload the DV Pre-Planilla export, or pick a period you already saved.
3. Check the headline figures against what the payroll system reported — gross, net, employee count. They should match.
4. Save the period so it can be looked at later without the original file.

### What the page shows you, and why each part exists

- **Totals per group** — payroll split by employee group.
- **Loan deductions** — money deducted from pay and passed straight on. **This is not a company expense.** It is somebody's loan repayment travelling through the payroll. Counting it as cost would overstate what the company spends.
- **Third-party deductions** — withheld and remitted onward. Around $1,625.78 per period historically.
- **Accrual breakdown** — each benefit line split into the part that is cash now and the part being accrued for later. This is what stops décimo appearing to come out of nowhere three times a year.

### A note on décimo

Décimo (the thirteenth month) is paid three times a year: **15 April, 15 August and 15 December**. Each payment is currently around $62,775.60.

On the forecast's current cash basis, these show up as three large lumps rather than being smoothed across the year. That is deliberate — it is what actually happens to the bank account, and December is precisely when the tool shows the year's tightest cash.

---

## Part 7 — Recording a termination

Do this as soon as a termination is confirmed, not when the payment is due. The forecast needs to know the money is coming.

1. Go to **Terminations**.
2. Enter the monthly salary, the hire date, the termination date, and the reason.
3. The tool calculates the full liquidación.
4. Save it.

The payment is automatically scheduled **30 days after the termination date**, which is company practice, and appears in the forecast on that day.

### The one thing to get right: the reason

**Indemnización is only paid on *Despido injustificado*.** Choosing the wrong reason changes the total substantially, so check it before saving.

What makes up the total:

| Component | Applies to |
|---|---|
| Prima de antigüedad | Every reason — one week per year of service |
| Indemnización | *Despido injustificado* only |
| Décimo proporcional | Every reason — accrued since the last décimo date |
| Vacaciones proporcionales | Every reason — 30 days per 11 months worked |

### When the payment has actually been made

Mark the termination **paid**. It then drops out of the forecast, because the real payment will appear in your daily log instead. If you do not mark it paid, that money is counted twice.

---

## Part 8 — The monthly routine

1. **Import the Sage income statement** on the Sage Actuals page.
2. **Compare forecast against actual** on the Daily Log page. Pick the month and look at the gap.
3. **Investigate anything large.** A big gap means either the assumption was wrong or something was not logged. Both are worth knowing.
4. **Review the expense schedule** on the Cash Flow page for lines that have started, ended or changed.

---

## Part 9 — Changing a cost properly (administrators)

This is the part most likely to be done wrong, and getting it wrong quietly produces a forecast that is too optimistic.

**A cost cut has a date.** If you are cancelling a $2,385 monthly service in October, the tool must not reduce August and September — that money really was spent.

On the **Cash Flow** page, in the expense schedule, each line has:

| Column | What to put in it |
|---|---|
| Expense | The name of the bill |
| Monthly amount | What it costs each month |
| Day of month | The day it is due |
| **Starts** | The first month it applies, as `2026-10`. Blank means it has always applied |
| **Ends** | The last month it applies, as `2026-09`. Blank means it continues indefinitely |
| **Flexible** | Tick if this bill could be delayed in a cash squeeze |

### To stop a cost from October

Set that line's **Ends** to `2026-09`. It will be charged through September and not from October.

### To reduce rather than remove a cost

Set **Ends** on the existing line to the last month at the old amount, then add a second line with the new amount and **Starts** at the following month.

### Why the Flexible tick is worth the effort

It lets the tool answer "what could I delay if I had to". A bill nobody has ticked is treated as immovable. Ticking the ones that genuinely could wait turns a cash problem into a list of options.

### Check your work

After any change, go to the Dashboard and look at the **lowest projected balance and the date**. If it did not move the way you expected, something is wrong — most often a date in the wrong format or a change applied to the wrong month.

---

## Part 10 — Using the AI Assistant well

Four tabs. It works in English and Spanish.

### Ask a question

Plain questions about the current numbers. *"When is cash tightest and how bad does it get?"* *"What is the biggest fixed cost?"*

### Try a change

Describe a change in words and see what it does. *"What if I cut the management fee from October?"*

Two things to know:

- **It never saves anything.** It shows you what would happen. To make a change real, go to the Cash Flow page and enter it yourself.
- **It will refuse things** — expense lines that do not exist, settings that are off limits, changes it cannot understand — and it tells you exactly what it refused rather than guessing.

### Monthly write-up

Narrative commentary on a month, useful as a starting point for reporting to somebody else.

### Log versus bank

Explains where the daily log and the bank disagree. Needs at least two recorded bank balances.

### What you should trust, and what you should not

**Every number it gives you is calculated by the tool itself, not by the AI.** The assistant is only allowed to talk about figures the forecast engine has handed it, and every reply is scanned for figures that were not supplied. This is deliberate: a language model doing arithmetic will occasionally be confidently wrong, which in a cash flow tool is dangerous.

So the numbers are reliable. But:

- **The interpretation is an opinion, not advice.** "You should draw on the credit line" is a suggestion to think about, not an instruction to follow.
- **It only knows what is in the tool.** It cannot know about the contract you signed yesterday or the client who is about to pay late.
- **If a figure looks wrong, check it on the page that owns it.** Trust the source pages over any summary.

The assistant is never shown employee names or individual pay.

---

## Part 11 — Common problems

| What you see | What to do |
|---|---|
| Page is slow to load first thing | The app sleeps when idle. Give it a moment |
| A code from my phone is refused | Wait for the next one. Each code works only once, and it may have just expired |
| The sidebar says data is going to temporary files | Tell Frank immediately. Data will be lost on restart |
| A button is greyed out | Your access level does not permit it |
| The Dashboard shows an old bank balance | Nobody has entered a newer one. Do it on the Daily Log |
| My cost cut has not changed the forecast | Check the **Starts** / **Ends** months on that line. Format is `2026-10` |
| The forecast looks far too good | Something is probably not in the expense schedule, or a cut was entered without an effective date |
| The AI Assistant says there is no key | The API key is missing from the app's settings. Tell Frank |
| I cannot see the Accounts page | It is administrators only, and it is hidden rather than greyed out |

---

## Part 12 — Rules that matter

1. **Enter the bank balance every day.** Without it the tool cannot check itself.
2. **Store your recovery codes away from your phone.** This is the only thing standing between you and being locked out.
3. **Never share an account.** If somebody needs access, they get their own.
4. **Give every cost change an effective date.** Otherwise the forecast reports savings that did not happen.
5. **Mark terminations paid once they are paid.** Otherwise the money is counted twice.
6. **Never send a password or a key by chat or email.** If one is ever exposed, say so straight away — it can be replaced in minutes, and the alternative is far worse.
7. **Edit on the page that owns the data.** The Dashboard and Forecast are read-only by design.

---

## Part 13 — Understanding the Dashboard

The figures, and what each is actually telling you.

| Figure | What it means |
|---|---|
| **Bank balance** | The most recent real balance somebody entered, with its date. Check the date |
| **Forecast starts from** | The cash the projection begins with. If it differs from the bank balance, the assumption is stale |
| **Balance in 30 days** | Where the projection expects you to be in a month |
| **Balance at end** | Where it ends at the horizon |
| **Lowest balance and date** | **The most important number on the page.** The tightest point ahead, and when it arrives |
| **Next 14 days in / out / net** | Immediate cash movement |
| **Monthly payroll, CSS, décimo** | The headline recurring costs |

### How to read the lowest balance

If it is **negative**, the projection says you run out of cash on that date. That is not a prediction of failure — it is a deadline. You have until then to either collect more, delay something flexible, or draw on the credit line.

If it is **comfortably positive**, check the date it occurs anyway. It moves as assumptions change, and it is the number to watch after every edit.

---

## Getting help

Ask Frank. When reporting a problem, say:

- Which page you were on
- What you were trying to do
- Exactly what the screen said

Never include your password or any key in the message.
