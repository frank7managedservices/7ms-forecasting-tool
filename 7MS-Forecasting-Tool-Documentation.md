# 7MS Forecasting Tool — Application Documentation

**Version:** as deployed 20 August 2026
**Live application:** https://btiuiztr3zzmfnxhnhbibj.streamlit.app
**Source repository:** https://github.com/frank7managedservices/7ms-forecasting-tool
**Owner:** Frank Royal, 7 Managed Services
**Document purpose:** Technical and functional reference. For day-to-day instructions, use the companion *Training Guide*.

---

## 1. What this application is

The 7MS Forecasting Tool is an internal cash flow forecasting and payroll analysis application. It answers one central question: **how much cash will the company have on any given day, and when does it run short?**

It does this by combining four things:

1. **Assumptions** you enter — expected collections, payroll cost, fixed bills and their due dates, line of credit terms.
2. **Real payroll data** imported from DV Pre-Planilla exports.
3. **Real accounting data** imported from Sage 50 (income statement and general ledger).
4. **Actuals you record daily** — money in, money out, and confirmed bank balances.

It then projects a day-by-day balance forward to a horizon you choose, highlights the lowest point, and compares what you predicted against what actually happened.

### What it is not

- It is **not** an accounting system. Sage 50 remains the book of record.
- It does **not** pay anybody or move money. It has no banking connection.
- It does **not** file or calculate statutory payroll returns. It reads what the payroll system produced.
- It is **not** a general-purpose HR system. Termination records exist only to get the cash impact into the forecast.

---

## 2. How it is built and hosted

| Layer | Technology | Notes |
|---|---|---|
| Application | Python with Streamlit | Single file, `app.py` (~4,900 lines), plus `payroll.py` |
| Hosting | Streamlit Community Cloud | Free tier. Redeploys automatically on every push to `main` |
| Source control | GitHub, repository `frank7managedservices/7ms-forecasting-tool` | Branch `main`, entry point `app.py` |
| Database | Neon serverless PostgreSQL, project **Cash Flow App**, region AWS `sa-east-1` | Connection string supplied as a secret |
| Data analysis | pandas | |
| Spreadsheet reading | openpyxl | |
| Database driver | SQLAlchemy with psycopg2 | |
| QR codes | qrcode (with Pillow, already present in Streamlit) | Used only for two-step sign-in enrolment |

### Why a separate database

Streamlit Community Cloud wipes the container's disk on every restart, and it restarts often. Anything worth keeping therefore goes to PostgreSQL. If no database is configured the application silently falls back to local JSON files so it still runs, but on the hosted deployment that fallback would lose data on restart. The sidebar always shows which mode is active, so you can tell at a glance.

### Required secrets

Set these in Streamlit Cloud under **App settings → Secrets**. Names are **case-sensitive** — this has caused a real outage before.

```
DATABASE_URL = "postgresql://USER:PASSWORD@HOST/neondb?sslmode=require&channel_binding=require"
OPENAI_API_KEY = "sk-..."
```

Optional:

```
OPENAI_MODEL = "gpt-5.6-luna"
```

`OPENAI_MODEL` exists because model names and prices change every few months. Changing a secret is faster and safer than changing code. If it is absent the application uses its built-in default.

`OPENAI_API_KEY` is only needed for the AI Assistant page. Every other page works fully without it.

### Deploying a change

1. Commit and push to `main`.
2. Streamlit Cloud detects the push and rebuilds, typically in one to three minutes. Longer if `requirements.txt` changed.
3. **Batch your commits.** Four pushes within fifteen minutes has previously wedged the container and required a manual reboot from the Streamlit dashboard.

---

## 3. Data storage

Two tables carry everything.

**`app_settings`** — one row per named object, body stored as JSON.

| Column | Meaning |
|---|---|
| `name` | The key, e.g. `cash_settings` |
| `body` | JSON payload |
| `saved_at` | Timestamp of last write |

Keys currently in use:

| Key | Contents |
|---|---|
| `cash_settings` | All forecast assumptions (see section 8) |
| `expense_schedule` | Fixed expense lines with due days and effective dates |
| `revenue_schedule` | Expected collections per month |
| `extra_revenue` | Additional revenue streams outside the core book |
| `agent_schedule` | Agent headcount plan used to derive billing |
| `actuals_ledger` | The daily log of real money in and out |
| `bank_balances` | Confirmed bank balances by date |
| `users` | Accounts, password hashes, two-step secrets, recovery code hashes |
| `require_2fa` | Whether two-step sign-in is compulsory for every account |

**`app_documents`** — imported source documents, so an import can be re-read later without the original file.

| Column | Meaning |
|---|---|
| `kind` | `payroll`, `sage_is`, `sage_gl` |
| `name` | The period label taken from the report header |
| `body` | The sheet converted to CSV text |
| `notes` | Free text |
| `saved_at` | Timestamp |

---

## 4. Access levels

Three levels, set per account on the Accounts page.

| Level | Can do |
|---|---|
| **admin** | Everything, including forecast assumptions and managing accounts |
| **user** | Record actuals, payroll, terminations and Sage figures. Cannot change forecast assumptions or accounts |
| **viewer** | Read every page. Cannot change anything |

Internally this reduces to two flags: `can_enter` (admin and user) controls whether data can be saved, and `can_configure` (admin only) controls the assumptions and the Accounts page.

The Accounts page is hidden entirely from user and viewer accounts — not merely disabled. The AI Assistant requires at least `user` level; a viewer is shown a notice and the page stops.

**Current state:** one account exists, `frank`, at admin level. There is no second administrator. See section 11 for the consequences.

---

## 5. Sign-in and account security

### Passwords

Stored as PBKDF2-HMAC-SHA256 with a random 16-byte salt per user and **200,000 rounds**. Plain passwords are never stored or logged. Minimum length on any change is 10 characters.

A wrong username and a wrong password produce the same message and take the same amount of time, so the sign-in screen cannot be used to discover which accounts exist.

Accounts created by an administrator get a temporary password and are flagged `must_change`. That account cannot reach any page until the password has been replaced.

### Two-step sign-in (TOTP)

Standard time-based one-time codes, compatible with Google Authenticator, Microsoft Authenticator and Authy. Implemented against RFC 6238 using only the Python standard library, deliberately avoiding a third-party dependency that could break a deployment. The implementation was verified against all six published RFC 6238 test vectors.

| Parameter | Value |
|---|---|
| Time step | 30 seconds |
| Digits | 6 |
| Drift tolerance | ±1 step, so a slightly wrong phone clock still works |
| Algorithm | SHA1 (as the authenticator apps expect) |
| Secret | 160-bit, base32 |

Protections in place:

- **Enrolment is proved before it is saved.** The secret is not written to the account until a valid code has been entered, so a bad QR scan cannot lock anybody out.
- **Codes cannot be replayed.** The last accepted counter is recorded per user, so a code that has been used once is refused thereafter. Someone reading a code over your shoulder cannot reuse it.
- **The password is checked first.** A wrong password never reaches the code screen. Between the two steps the application holds only the login name in session — never the password.

Enrolment status and remaining recovery codes are visible to an administrator on the Accounts page. An administrator can make two-step compulsory for every account; this switch is **currently on**, so any new account will be required to enrol before it can do anything.

### Recovery codes

Eight one-time codes are issued at enrolment, formatted in groups of four characters so they can be written down. They are stored hashed exactly like passwords, so a stolen copy of the database yields no usable code. They are displayed **once**, with a download button, and cannot be retrieved afterwards. A fresh set can be issued at any time from My Account, which invalidates the old set.

A recovery code substitutes for the phone at the code screen. Each is consumed on use.

### Forgotten password

The sign-in screen has a **Forgot my password** box. It requires the username, **two different valid recovery codes**, and the new password twice.

Design decisions worth recording:

- **Two codes, not one.** A single recovery code already satisfies the second step. Allowing that same code to also reset the password would make one lost slip of paper a complete account takeover.
- **Both codes are validated before either is spent**, so a right code paired with a wrong one does not silently consume the good one.
- **The same code cannot be used twice** in one reset.
- **A reset does not sign anyone in and does not bypass two-step.** The user returns to the sign-in screen and still faces the code step.
- **Failure messages are deliberately vague** and identical for an unknown username and a wrong code.
- The user is warned when three or fewer recovery codes remain.

Accounts without two-step have no recovery codes and therefore cannot self-reset; they must be reset by an administrator.

### Administrator rescue

For someone genuinely locked out — lost phone, lost codes — an administrator can switch two-step off for that account from the Accounts page. Doing so also discards their recovery codes. The action requires ticking a confirmation that the administrator has verified who they are talking to, because an unsolicited phone call asking for two-step to be disabled is the standard way accounts are stolen.

### Other behaviour

If an account is deleted while that person is still signed in, their session is dropped with a clear message rather than leaving them on a blank page.

---

## 6. The pages

### Dashboard

Read-only summary. Bank balance and its date, the cash the forecast starts from, balance in 30 days, balance at the end of the horizon, the **lowest projected balance and the date it occurs**, money in and out over the next 14 days, and the headline monthly costs. All editing happens on the page that owns the data.

### Forecast

The day-by-day and month-by-month projection produced by the forecast engine (section 7), with the underlying table available for inspection and download.

### Payroll

Import a DV Pre-Planilla export, or select a previously saved period. Produces:

- Headline totals for the period — gross, net, employee count
- Totals per employee group
- Pass-through **loan deductions**, identified separately because they are not a company expense
- **Third-party deductions**, withheld and remitted onward
- An **accrual breakdown** splitting each benefit line into the part that is cash now and the part that is being accrued
- Automatic detection of how often the planilla runs, read from the report header rather than assumed

`EMPLOYEE_OVERRIDES` pins specific employee classifications where the source data is wrong; it currently maps employee 218353 to President (Mary Royal).

### Terminations

Record a termination and get the full liquidación calculation under Panama labour law (section 9). Saved terminations feed their scheduled payment dates into the forecast automatically.

### Sage Actuals

Import a Sage 50 12-period income statement or a general ledger export. The income statement gives revenue, cost of sales, expenses and net income per period. The ledger is parsed to one row per transaction and can be summarised by account or by month. Ledger categories are mapped onto forecast buckets so actuals and forecast can be compared like for like.

### Cash Flow

Where an administrator sets every assumption: opening cash, collections and their timing, payroll and statutory costs, the fixed expense schedule, the projection horizon, cash or accrual basis, and the line of credit. Settings can be exported to `cash_settings.json` as a backup.

### Daily Log

Record what actually happened — money in, money out, and today's confirmed bank balance. Also shows **forecast beside actual** for a chosen month with the gap between them. This page is the input that makes the whole tool self-correcting; without it the forecast is only ever a guess that is never marked.

### AI Assistant

Four tabs (section 10). Requires `user` level or above and an API key.

### My Account

Available to every account. Change your own password, set up or manage two-step sign-in, and issue fresh recovery codes.

### Accounts

Administrator only. Add, edit and remove accounts, set access levels, reset passwords, view two-step status and remaining recovery codes, make two-step compulsory, and perform the rescue described in section 5. The application refuses to remove or demote the last remaining administrator.

---

## 7. The forecast engine

`build_schedule` walks forward one day at a time from today to the horizon, applying each cash movement on the day it actually occurs, and records the running balance.

### Timing rules

| Item | When it lands |
|---|---|
| Payroll | Half on the 15th, half on the last day of the month |
| CSS / government | Last day of the month |
| Pluxee | 15th |
| Viático | 15th |
| Décimo (cash basis) | 15 April, 15 August, 15 December |
| Fixed expenses | Each line on its own due day |
| Line of credit interest | Month end, on the drawn balance |
| Severance / liquidación | The scheduled payment date from the Terminations page |
| Collections | Spread evenly, or on a chosen day each month, or per-month from a schedule |

Anything scheduled for a day that does not exist in a short month — a bill due on the 30th in February — lands on the last day of that month instead of being skipped.

### Cash basis versus accrual basis

- **Cash basis:** money leaves on the day it actually leaves. Décimo hits as three lump payments. Vacation is charged only when somebody is actually paid out.
- **Accrual basis:** décimo and vacation are charged as smooth monthly provisions at month end, the way the books carry them, and the three décimo lump payments are *not* charged again because they were already provisioned.

Over a full year both bases move the same total; they distribute it differently. Cash basis answers "will the bank account survive". Accrual basis matches the accounting. The application currently runs on **cash basis**.

### Effective dates on expenses

Every expense line can have a start month and an end month. This matters more than it sounds: a cost cut taking effect in October must not reduce August's costs. The engine therefore recalculates what is due **per month** rather than once for the whole projection.

Each line can also be marked **Flexible**, meaning it could be delayed if cash were tight. The application can list flexible bills falling due in a given window, which is the practical answer to "what can I push".

### Line of credit

| Setting | Behaviour |
|---|---|
| Limit | Maximum total that can be drawn |
| Already drawn | Opening drawn balance |
| Annual rate | Interest charged monthly on the drawn balance |
| Automatic draw | If enabled, tops cash back up whenever a day would otherwise fall below your floor |
| Minimum cash floor | The level the automatic draw protects |
| One-time draw | A specific amount on a specific date |

Automatic draws are rounded up to whole thousands, because that is how a bank transfer actually happens. Draws never exceed the remaining availability.

---

## 8. Settings reference

Current live values, for reference and disaster recovery.

| Setting | Value | Meaning |
|---|---|---|
| `start_cash` | 13,367.94 | Cash the forecast starts from |
| `revenue` | 318,585.16 | Expected collections per month |
| `revenue_mode` | One day per month | How collections are distributed |
| `revenue_day` | 20 | Day collections land |
| `revenue_lag` | 0 | Days between billing and collection |
| `horizon` | 136 | Days projected forward |
| `end_date` | 2026-12-31 | Target end of projection |
| `basis` | Cash basis | Cash or accrual |
| `expense_mode` | Use my due-date schedule | Schedule, or spread evenly |
| `payroll` | 153,881.00 | Monthly payroll, split across two pay dates |
| `css` | 88,144.25 | Monthly CSS / government |
| `pluxee` | 19,000.00 | Monthly, on the 15th |
| `viatico` | 10,000.00 | Monthly, on the 15th |
| `decimo` | 62,775.60 | Per décimo payment |
| `fixed` | 179,603.04 | Monthly other fixed costs, used when not on the schedule |
| `decimo_provision` | 0.00 | Monthly accrual, accrual basis only |
| `vacation_provision` | 0.00 | Monthly accrual, accrual basis only |
| `loc_limit` | 200,000.00 | Line of credit limit |
| `loc_drawn` | 0.00 | Currently drawn |
| `loc_rate` | 8.0 | Annual percentage |
| `loc_auto` | true | Automatic top-up enabled |
| `loc_min_cash` | 10,000.00 | Cash floor protected |
| `loc_draw_amount` | 200,000.00 | One-time draw |
| `loc_draw_day` | 2026-09-15 | Date of the one-time draw |

### Expense schedule

Thirteen lines, columns `Expense`, `Monthly amount`, `Day of month`, `Starts`, `Ends`, `Flexible`.

| Expense | Amount | Day |
|---|---|---|
| Rent | 33,727.56 | 5 |
| Management Fee | 20,000.00 | 5 |
| Lawyer | 650.00 | 5 |
| Utilities | 4,500.00 | 10 |
| Insurance | 4,500.00 | 15 |
| Dario | 4,500.00 | 15 |
| `supplies` | 2,000.00 | 15 |
| Everything else | 3,000.00 | 25 |
| `payroll` | 3,300.00 | 25 |
| Tech | 2,385.00 | 25 |
| SIPE CSS payment | 11,156.87 | 25 |
| Dario (2) | 4,500.00 | 30 |
| Compulabs | 1,400.00 | 30 |

Total 95,619.43 per month.

Two naming and data-quality points, accurate as of this document:

- The line named `payroll` is the **payroll service fee**, not payroll itself. Payroll is a separate setting entirely. The name is misleading and renaming it is an open task.
- **No line currently has a Starts or Ends month, and none is marked Flexible.** Until effective dates are entered, planned cost reductions do not appear in the projection at all, and the tool cannot identify which bills could be delayed. This is the single largest gap between the forecast and current intentions.

---

## 9. Termination and liquidación calculations

Panama labour law. Reasons supported: Despido injustificado, Despido justificado, Renuncia, Mutuo acuerdo, Jubilación.

### Salary basis

| Quantity | Formula |
|---|---|
| Weekly salary | monthly × 12 ÷ 52 |
| Daily salary | monthly ÷ 30 |
| Years of service | days between hire and termination ÷ 365 |

### Components

**Prima de antigüedad** — one week of salary per year of service, for every reason.

**Indemnización** — applies **only** to *Despido injustificado*, under Article 225:

- 3.4 weeks per year for the first 10 years
- plus 1 week per year beyond 10 years

**Décimo proporcional** — accrued since the most recent décimo date on or before the termination (15 April, 15 August or 15 December), or since a date you specify.

**Vacaciones proporcionales** — 30 days of vacation per 11 months worked, valued at the daily salary. Can be overridden with an explicit day count where the payroll system already holds the accrued figure.

**Total** = prima + indemnización + décimo proporcional + vacaciones.

### Payment timing

Payment is scheduled **30 days after the termination date**, matching company practice. Unpaid terminations feed automatically into the forecast on that date; once marked paid they drop out, because the real payment will appear in the daily log instead and would otherwise be double-counted.

---

## 10. AI Assistant

### The governing rule

**The language model never calculates any number.** Every figure it is allowed to mention is computed by the same forecast engine that drives the rest of the application and handed to it as established fact. This is the whole design, and the reason is simple: a language model that does arithmetic will occasionally do it wrong and state the result with complete confidence, which in a financial tool is worse than useless.

Two mechanisms enforce it:

- `assistant_facts` assembles a compact, honest picture of the numbers. It is the **only** thing the model is shown. It deliberately excludes employee names and individual pay.
- `audit_numbers` scans every reply and flags any figure that was not in the facts supplied. During testing this correctly caught a deliberately planted fabricated figure.

### The four tabs

1. **Ask a question** — plain questions about the current numbers.
2. **Try a change** — describe a change in words; the application parses it into concrete parameter changes, runs the real forecast, and reports the difference month by month. It **never saves** anything, and it refuses unknown expense lines, off-limits settings and change types it does not recognise, reporting exactly what it did and what it refused.
3. **Monthly write-up** — narrative commentary on a month.
4. **Log versus bank** — where the daily log and the confirmed bank balance disagree, with the likely causes worked out rather than guessed. Requires at least two recorded bank balances.

Available in English and Spanish. Degrades gracefully with no API key: the page explains what is missing instead of failing.

### Cost

The default model is inexpensive — under roughly two dollars a month at fifty questions a day, on the pricing that applied when it was chosen. Set `OPENAI_MODEL` to change it without touching code.

---

## 11. Known limitations and risks

**Single administrator.** `frank` is the only admin account. Nobody can reset his password for him. The mitigation chosen is the self-service reset in section 5, which depends entirely on the recovery codes being stored somewhere safe and off the phone. If those codes are lost and the password is forgotten, recovery requires direct database access.

**The repository is public.** Never commit payroll data, bank data, employee data or credentials. All real data lives in the database or in secrets, never in the repository.

**Outstanding credential rotation.** The Neon database password has been exposed — in the repository's git history, and later in a support conversation. It was removed from the working tree, but git history retains it. Rotating it is an open task: reset the `neondb_owner` role in the Neon console, then paste the new connection string straight into Streamlit Secrets without it passing through any chat or email.

**Unpinned dependencies.** `requirements.txt` names packages without versions, so a future breaking release upstream could break a deploy without any change on our side. Pinning versions is an open task.

**Free-tier hosting.** Streamlit Community Cloud sleeps when idle and can take a moment to wake, and rapid successive deploys can wedge the container.

**No audit trail.** Who changed which assumption and when is not recorded.

**Forecast quality depends on inputs.** The engine is only as good as the assumptions. In particular, planned cost reductions must be entered with effective dates before the projection reflects them.

---

## 12. Testing

Four test suites, 154 checks in total, all passing as of this document. They must be run before every push.

| Suite | Checks | Covers |
|---|---|---|
| `test_pages.py` | 49 | Every page renders for every access level, all controls, sign-in gate, access restrictions |
| `test_2fa.py` | 31 | Enrolment, the code step, replay refusal, recovery codes, admin rescue, compulsory mode, leak checks |
| `test_reset.py` | 48 | Forgotten-password flow including every failure path |
| `test_ai.py` | 26 | Assistant logic, the number auditor, scenario refusals |

Run with a real database URL supplied so the tests exercise real data:

```
cd /path/to/7ms
REAL_DATABASE_URL="postgresql://..." python -u test_pages.py
```

Each suite prints a verdict line — `ALL PAGES CLEAN`, `TWO-STEP CLEAN`, `RESET CLEAN`, `ASSISTANT LOGIC CLEAN` — and exits non-zero on any failure.

Two testing notes worth keeping, both of which have already caused confusion:

- The suites share one SQLite mirror. `test_2fa.py` leaves compulsory two-step switched on, which would put every page behind the enrolment screen in a later suite. `test_pages.py` now resets that switch before each page check.
- Buttons must be clicked by **label**, not by index. The sign-in screen's first button is *Sign in*, not the one you probably mean.

---

## 13. Maintenance checklist

**Before every push**
1. Run all four test suites; all must be clean.
2. Scan changed files for credentials — no `npg_`, no `sk-`.
3. Batch changes into one commit.

**Monthly**
1. Import the Sage income statement and reconcile against the forecast.
2. Review the expense schedule for lines that have started or ended.
3. Confirm the bank balance on the Daily Log.

**Quarterly**
1. Review accounts and access levels; remove anyone who has left.
2. Confirm every account still has recovery codes remaining.
3. Re-export `cash_settings.json` as a backup.

**As needed**
- Rotate credentials on any suspicion of exposure.
- Pin dependency versions before any significant upstream release.
