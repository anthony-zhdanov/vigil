# Business Context — Missed-Call Recovery, Managed (Plumbing Wedge)

> **Purpose of this document.** Single source of truth for the business. Designed to be pasted into a Claude Project, a custom GPT, or any future thread so the assistant has full context without re-deriving it. Supersedes prior `AI Overview` and `MVP v2` documents where they conflict — the resolutions are explicit below.
>
> **Last updated:** 2026-05-16
> **Status:** Pre-launch. RAT (Riskiest Assumption Test) not yet run.

---

## 1. Business at a glance

**One-line description.** A managed lead-recovery service for independent plumbing shops in the Greater Toronto Area that turns their existing missed calls and stale quotes into booked jobs via SMS and email workflows, with a weekly dollarized report.

**Founder profile.** Solo, sales-led, no existing trades-owner network, ~20 hrs/week available, ~$150/month operating budget pre-revenue.

**Geography.** Greater Toronto Area, sourced from a 50–100km radius around Toronto. Excludes Burlington, Grimsby, Dundas, Hamilton, and Beamsville from the primary target market.

**Vertical.** Plumbing only at launch. HVAC reserved as fallback vertical if plumbing motion fails (see §9).

**Verdict from pre-launch scoring.** TEST — score 67/100, high confidence. Demand is real and pricing is well-calibrated, but the market is saturated and the launch channel is unproven for this founder profile. The score is "test, don't build" — do not invest in tooling beyond free trials until the RAT passes.

---

## 2. The service (MVP)

The MVP is **SMS and email follow-up on missed calls and stale quotes pulled from the owner's existing system**. It does not include a live AI receptionist — that is explicitly Post-MVP and only sold after a customer is established (see §3). This is a deliberate departure from earlier drafts that bundled live call answering into the MVP.

### 2.1 Free 7-day audit (the door opener)

Before they pay anything, prove what they're losing.

- Track or pull the prospect's last 7 days of inbound calls, voicemails, and outstanding quotes
- Deliver a one-page report and a short Loom walkthrough: "You missed *N* calls last week. Based on your average ticket, that's approximately $X in unrecovered revenue. The three biggest leaks are A, B, and C."
- Free. Friction-free. The audit is the cold-outreach hook, not a profit center.
- Tooling: free-tier Aircall or Google Voice trial + Notion tracker. Cost ceiling for the entire RAT phase: **$80**.

> **Loom** is a free screen-recording tool ([loom.com](https://www.loom.com)) that records your screen + voiceover and gives you a shareable link. Used because async video is higher-trust than a written email for trades owners and faster to produce than a live call. A 20-minute Loom is fine; longer is worse.

> **Edge case — non-standard stacks.** Some shops run their entire intake through WhatsApp, a personal cell phone, or a paper book with no digital trail. For the audit, ask the owner to forward calls to a tracking number for 7 days, or screen-share their phone's call log live. If they refuse both, they aren't an audit-stage prospect yet — note them as future-stage and move on.

### 2.2 Lead Recovery Setup (Weeks 2–4 after pilot signup)

- Connect to their existing call log, CRM, and quote system via API or shared inbox (Jobber, Housecall Pro, QuickBooks, Google Voice, or improvised)
- Install SMS and email follow-up workflows tuned to their services, hours, and service area:
  - Missed-call texts within 60 seconds, with urgency detection (emergency vs. quote request vs. tire-kicker)
  - Stale-quote follow-ups at intervals that actually convert (typical: 24h, 72h, 7d, 14d)
  - Message templates written in the owner's voice, not generic AI copy
- Define rules for what gets followed up, what escalates to the owner, what gets dropped
- Set up the weekly dollarized report (jobs recovered, dollars recovered, what changed, what to try next)
- Owner signs off on every workflow before it goes live

### 2.3 Monthly Operating Retainer (ongoing)

- Run and maintain the follow-up workflows
- Tune messages and timing based on what's converting
- Add seasonal playbooks (furnace season, AC season, frozen pipes, spring HVAC tune-ups — relevant even for plumbing-only shops that cross-sell)
- Absorb the integration tax: webhook breaks, phone number changes, new service categories, software updates on their end
- Weekly dollarized report delivered every Monday morning
- Monthly call with the owner to review the number and decide next moves

---

## 3. Post-MVP (sold only after 60+ days of proven recovery)

Do not pitch these on cold outreach. They exist as expansion revenue after the case study is established.

- AI receptionist for overflow and after-hours calls, with human handoff rules
- Quote-builder follow-up sequences tied to specific job types
- Review-request automation after completed jobs
- Reactivation campaigns for customers who haven't booked in 12+ months

---

## 4. Pricing

The pricing model has **two phases**, deliberately structured to give the cold-outreach pitch a sharp wedge while avoiding permanent attribution disputes.

### Phase A — First 90 days with a new client (outcome pricing)

| Component | Price |
|---|---|
| Audit | Free (7 days) |
| Setup | $499 (one-time, paid on pilot signup) |
| Monthly base | $299 |
| Per-recovered-job fee | $15 per booked job traceable to the recovery workflows |

This is the wedge for cold outreach: *"Every other service charges you whether they get you jobs or not. We charge $15 per job we actually recover. If we don't get you jobs, your base fee is $299 — and the audit will show you exactly what that buys."*

Outcome pricing is rare in this category — no major competitor (Ruby, Smith.ai, Numa, Goodcall, Rosie, Avoca) charges per-recovered-job. That asymmetry is the sharpest differentiator on a cold call.

### Phase B — After 90 days, with a documented case study (flat retainer)

Convert the client to a flat monthly retainer (typically $499–$799/month, set based on their actual recovered volume during Phase A). This:

- Kills the attribution dispute ("would this booking have come anyway?") before it metastasizes
- Smooths revenue for the founder
- Locks in the relationship at a price the owner has already seen the ROI on

Concierge tier (live human reply 7am–10pm, priority same-day response) remains a $799/mo upsell available at any point.

### Unit economics (planning assumptions)

- LTV ≈ $3,250 (assuming ~10mo retention at blended $299 base + $15/job activity)
- Payback < 1 month on cold outreach
- Re-score these after 90 days of real data; the 67/100 verdict is a pre-launch projection

---

## 5. The cold-outreach plan (RAT phase)

The Riskiest Assumption Test. Run in the next 14 days. Total spend ceiling: **$80**.

### 5.1 Riskiest assumption being tested

*"A sales-led founder with no trades-owner network can convert solo trades to paid pilots via 100% cold outreach + a free 7-day audit at a rate that supports the unit economics — with zero paid acquisition budget."*

### 5.2 Channel

**Phone calls first, in-person at supply-house counters second. Email last (or not at all).** Trades owners ignore cold email. Phone and in-person have meaningfully higher pickup and trust rates with this buyer.

The 200-shop spreadsheet (`GTA_Plumbing_Contacts_Database.xlsx`) is the target list. Tier 1 first, then 2, then 3, and so on. Do not skip tiers — Tier 1 is the most likely to convert and validates the motion fastest.

### 5.3 Target market (from the contacts database)

**Primary market (86 businesses):** 20–200 review counts, ≥4.7 Google rating, within 50km of Toronto, excluding Burlington, Grimsby, Dundas, Hamilton, Beamsville.

**Secondary market (114 businesses):** any review count, any rating, within 100km. Used only as fallback if primary market is exhausted before the test concludes.

### 5.4 Prioritization tiers

| Tier | Count | Profile |
|---|---|---|
| 1 | 13 | 80–200 reviews, 4.8–5.0 ★, Toronto / North York / Scarborough |
| 2 | 25 | 80–200 reviews, 4.7+ ★, expanded GTA |
| 3 | 29 | 40–79 reviews, 4.7+ ★, expanded GTA |
| 4 | 19 | 20–39 reviews, 4.7+ ★, expanded GTA |
| 5 | 24 | 20–200 reviews, outer regions |
| 6 | 90 | Remaining contacts |

The spreadsheet has one tab per tier. Each row contains business name, phone, address, city, region, Google rating, review count, and a notes column for call outcomes.

### 5.5 Test parameters

- **Contacts to attempt:** 50 in 14 days
- **Channel:** phone call or supply-house counter visit
- **Pitch:** free 7-day audit
- **Tracking:** Notion tracker (one row per contact: name, channel, outcome, audit accepted Y/N, pilot signup Y/N)

### 5.6 Pass / kill criteria

- **PASS:** ≥3 audit acceptances AND ≥1 paid-pilot signup from the 50 contacts.
- **KILL:** complete 50 contacts in 14 days with <3 audit acceptances OR zero pilot signups → cold outreach motion does not work for this founder/market combination. Stop and pivot (see §9).

---

## 6. What you're explicitly NOT selling

Reinforce this in every conversation with prospects:

- No software to learn
- No dashboard to log into
- No additional subscription to manage
- Nothing that requires them to change how they work

They forward their number (or grant inbox access). The service handles everything. They get a report every Monday. That is the entire ask on their end.

---

## 7. Competitive landscape and positioning

### 7.1 Who's already in this space

- **Direct AI receptionist vendors:** Avoca AI ($1B valuation), Numa, Goodcall, Rosie, Sameday, Smith.ai, Ruby, Beside ($20M raised Nov 2025 specifically for this wedge), Dialpad AI, ~40+ others
- **FSM platforms bundling native AI receptionist:** ServiceTitan, Housecall Pro, Jobber. Bundling threat is real on 12–24 month horizon (see §8)
- **GoHighLevel agencies:** hundreds of marketing agencies pitching "managed + outcome-based" on TikTok, often bundled with Google Business Profile and reviews at ~$399/mo all-in

### 7.2 The wedge — what's actually defensible

1. **Outcome pricing ($15/recovered job for the first 90 days).** No major player owns this position. Cold-call sharp.
2. **Managed delivery, not self-serve.** 67% of contractors who buy AI receptionist tools quit within 90 days because nobody manages exceptions. Doing the work for them is the moat against self-serve tools.
3. **Local + human + weekly dollar report.** Marketing agencies show vanity dashboards. Per-call vendors show minutes used. The weekly dollarized report ("we made you $4,200 last month") is what makes the retainer impossible to cancel.
4. **Sales-led founder.** Most competing GoHighLevel agencies are run by marketers without sales chops. Cold pitching, audit delivery, and consultative renewals are exactly the work this founder profile is suited for.

### 7.3 Where the wedge is thin

- The wedge is **positioning**, not technology. A well-funded competitor could replicate the pricing model in a quarter.
- The wedge weakens at scale. Once you're trying to land a 20-truck operation, ServiceTitan and its ecosystem become the real fight.

---

## 8. Top risks (with mitigations)

### Risk 1 — Competition (32/100)

Market saturation is at maximum. Owners may not be able to distinguish this offer from the agency that DM'd them last week. **Mitigation:** lead every cold call with the outcome-pricing line ("we charge $15 per job we actually recover"). It is the only positioning that doesn't sound like every other cold pitch.

### Risk 2 — Distribution (50/100)

Exactly one viable channel (cold outreach + free audit) and zero backup. No audience, no warm intros, no paid acquisition budget. **Mitigation:** the RAT itself is the mitigation — find out fast whether the channel works before investing further. If it fails, §9 is the fallback.

### Risk 3 — Retention (58/100)

Attribution disputes ("this booking would have come anyway") and FSM bundling threats compound on a 12–24 month horizon. Jobber and Housecall Pro clients will see "free AI receptionist" appear at renewal. **Mitigations:**
- Flip from outcome pricing to flat retainer at day 90 to kill the attribution dispute before it has time to compound
- Weekly dollarized report makes the value visible and continuous, raising the perceived switching cost when a free-bundled receptionist appears
- After Stage 2, narrow target ICP to shops on QuickBooks-only or improvised stacks (no FSM bundle to compete with)

---

## 9. Roadmap and decision gates

### Stage 0 — RAT (Weeks 1–2)

- Cold-contact 50 plumbing shops from the database
- Pass: ≥3 audits + ≥1 pilot. Then proceed to Stage 1.
- Kill: stop and pivot. See "If RAT fails" below.

### Stage 1 — Prove delivery (Weeks 3–6)

After 1 paid pilot is signed.

- Wire up real stack: chosen AI receptionist trial (Numa / Goodcall / Rosie) + scripts + human-reply backup, *only* if needed for delivery — MVP is SMS/email, voice is optional in Stage 1
- Run pilot for 30 days, track every missed call → text-back → reply → booked job
- **Pass criterion:** end-of-month report shows ≥5 recovered jobs at ≥$1,200 avg ticket. That's the ROI proof and the case study.
- **Kill criterion:** can't reliably recover jobs at the industry-benchmark ~45% text-back success rate → the wedge is fake regardless of sales skill.

### Stage 2 — Scale the motion (Months 2–3)

Next riskiest assumption: *"Solo sales-led founder with no trades network can land 5 pilots in 60–90 days at 20 hrs/wk."*

- Repeat the RAT playbook at 4× volume
- Goal: 5 paying clients by end of month 3 (~$1.5K MRR at base alone)

### Stage 3 — Test the moat (Months 4–6)

Next riskiest assumption: does outcome pricing + monthly ROI report actually defend against agency competition?

- Track churn on the first 5 clients
- Begin converting Phase A clients to Phase B flat retainers as they hit day 90
- **Decision point at month 6:** ≥5 retained clients at <10% monthly churn → go full-time. >20% churn or stuck below 5 clients → re-run pivot analysis.
- Re-score the business with real data after Stage 2. The 67/100 was a pre-launch projection; expect ±15 points of movement.

### If RAT fails

Pre-identified pivot options in order of preference:

1. **Different vertical, same motion.** HVAC during seasonal surge (Sept–Nov or Mar–May). Plumbing shops were chosen for density and per-job ACV; HVAC has higher per-job ACV but more seasonal.
2. **Same vertical, different motion.** Replace phone outreach with in-person supply-house partnerships (build relationships at counters where plumbers shop daily; trade per-introduction commission for distribution).
3. **Distribution model change.** Agency white-label, FSM-consultant referral partnerships, or narrowing to ex-Numa/Goodcall churned customers (different distribution models, not just different copy).

---

## 10. Pre-mortem — how this fails in 12 months

The three most likely failure modes, named so they can be watched for:

1. **The cold channel stalls.** Audit→pilot conversion comes in under 2% across the first 100 attempts. Three months in: 2 pilots, no paying clients. Side-project willpower runs out before the channel finds its rhythm.
2. **Bundling kills MRR at month 12.** 8 clients land, retention looks great through month 6, then 4 hit Jobber Plus renewal and consolidate to a bundled $39/mo receptionist. ARR halves.
3. **Agency competition compresses pricing.** Local GoHighLevel agencies undercut with bundled "missed-call + GBP + reviews" packages at $399/mo all-in. The $299/mo + $15/job structure feels expensive and narrow by comparison.

---

## 11. Glossary

- **RAT** — Riskiest Assumption Test. The smallest, cheapest experiment that can validate or kill the biggest unknown.
- **FSM** — Field Service Management software. Jobber, Housecall Pro, ServiceTitan are the leaders.
- **ICP** — Ideal Customer Profile.
- **ACV** — Annual Contract Value (here, used loosely to mean per-job revenue).
- **MRR / ARR** — Monthly / Annual Recurring Revenue.
- **LTV** — Lifetime Value of a customer.
- **GBP** — Google Business Profile.
- **Loom** — Async screen-recording tool used for the audit walkthrough. [loom.com](https://www.loom.com)
- **GoHighLevel** — White-label marketing-automation platform commonly used by small agencies pitching local businesses.

---

## 12. Open questions and known unknowns

These are unresolved and worth revisiting after the RAT:

- **WhatsApp-only shops.** If a meaningful share of target prospects run intake entirely through WhatsApp or personal cell, the call-log audit becomes harder. Workaround exists (forwarded tracking number) but hasn't been tested.
- **The $15/recovered-job attribution method.** Define before pilot #1: is "recovered" any booking whose first touch came through the workflow, or only bookings where the customer wouldn't have called back otherwise? Pick one and write it into the contract. Ambiguity here is the most common attribution-dispute trigger.
- **Concierge tier (human reply 7am–10pm).** Listed in original pricing tiers at $799/mo but operationally requires either the founder being on-call or hiring a contractor. Defer until at least 3 paying clients exist.
- **Provincial regulation on automated SMS in Canada.** CASL (Canada's Anti-Spam Legislation) applies to commercial SMS. Confirm consent flow with each pilot before going live; existing customers who called the shop have implied consent for response, but stale-quote follow-ups beyond ~6 months may not.
