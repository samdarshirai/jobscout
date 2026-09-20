# FINDINGS

## Outcome metric

Spearman rank correlation (Agent overall vs the Candidate's overall label), production run, n=32: **0.702** (target ≥ 0.6, met).

Knockout false-exclusion count: **3** (a Posting she thumbed up that a Knockout excluded -- the worst failure mode, target 0).

- `arbeitnow:senior-full-stack-engineer-frontend-leaning-platform-team-berlin-362893`: her overall 56, thumb up, excluded
- `ats:neoshare:neoshare:2449961`: her overall 92, thumb up, excluded
- `ats:neoshare:neoshare:2752998`: her overall 92, thumb up, excluded

## Bake-Off

| Model | Mechanism | n | Spearman | Cost/posting |
|---|---|---|---|---|
| deepseek-chat | both | 30 | 0.591 | $0.0044 |
| deepseek-chat | few-shot | 30 | 0.516 | $0.0048 |
| deepseek-chat | none | 30 | 0.128 | $0.0046 |
| deepseek-chat | summary | 30 | 0.478 | $0.0048 |
| frontier-anchor | both | 25 | 0.595 | $0.0334 |
| frontier-anchor | few-shot | 21 | 0.598 | $0.0341 |
| frontier-anchor | none | 20 | 0.487 | $0.0326 |
| frontier-anchor | summary | 30 | 0.763 | $0.0298 |
| kimi | both | 30 | 0.847 | $0.0122 |
| kimi | few-shot | 30 | 0.757 | $0.0119 |
| kimi | none | 30 | 0.792 | $0.0120 |
| kimi | summary | 30 | 0.791 | $0.0122 |
| qwen | both | 30 | 0.091 | $0.0048 |
| qwen | few-shot | 30 | -0.120 | $0.0044 |
| qwen | none | 30 | 0.086 | $0.0046 |
| qwen | summary | 30 | 0.171 | $0.0036 |

## Ablation deltas

| Ablation | n | Spearman | Delta vs. production | |
|---|---|---|---|---|
| feedback-off | 21 | 0.608 | -0.093 | helps (removing it hurt) |
| matched-lines-off | 27 | 0.752 | +0.050 | costs correlation, by design |

## Drift-closure curve

Pending -- needs the Candidate's round-2 end-of-project re-label (~15 Postings, DESIGN §15) to measure her own drift and whether the Preference Feedback Loop closed the gap. No round-2 labels exist yet.

## Failure-mode writeups

10 of 32 Postings diverged (|agent − human| ≥ 25) and were triaged by the calibrated judge:

- **she_is_outlier**: 7
- **weak_rationale**: 3

- `arbeitnow:senior-full-stack-engineer-frontend-leaning-platform-team-berlin-362893` (agent 26 vs her 56, diff 30) -- *weak_rationale*: The Agent's reasoning has an internal logical flaw: it claims the candidate scores 0 on the stack fit rubric criterion ('React/Vue/Svelte primary or no Angular'), yet then assigns maximal scores to the other three dimensions to reach 26/100. However, if the candidate's Angular background was truly disqualifying under the stated rubric, the criterion should have been applied consistently across all candidates or explicitly downweighted. The Candidate's rationale—penalizing for lack of explicit frontend framework mention rather than penalizing the candidate's Angular history—is actually more internally consistent, even if conservative.
- `ats:Camunda:camunda:8e48fc26-68f2-4754-bfff-c29a100dfe90` (agent 37 vs her 0, diff 37) -- *she_is_outlier*: The Agent provides detailed technical analysis identifying genuine skill mismatches (Java/BPM/PL-SQL/Oracle/DB2/Blue Prism requirements vs. candidate's modern Angular specialization) and role-fit concerns (post-sales/consulting engineer vs. frontend architect), which justify a low score. The Candidate's reductive dismissal of 'sales role and not engineering' ignores that field engineering roles legitimately involve engineering work and overlooks the substantive technical stack incompatibilities the Agent identified.
- `ats:Celonis:celonis:7805685003` (agent 64 vs her 90, diff 26) -- *she_is_outlier*: The Agent's rationale is thorough and defensible: the candidate's technical stack matches the JD nearly line-for-line, their enterprise SaaS experience aligns well, and their architecture/mentoring scope fits the role requirements. The Agent correctly identifies this as a strong match (5.00/5.0) but applies a location gating flag due to prior Madrid experience. The Candidate's 90/100 score based solely on location being Madrid rather than Germany is actually the outlier judgment—they dismiss an otherwise excellent technical fit on a single geographic criterion that may be negotiable or misunderstood, whereas the Agent's lower score reflects reasonable caution about a constraint rather than technical misalignment.
- `ats:Solarisbank:solarisbank:8596522002` (agent 26 vs her 0, diff 26) -- *she_is_outlier*: The Agent's rationale is internally consistent and methodically applies its scoring rubric across multiple dimensions (stack fit, domain, scope, culture), reasonably penalizing the mismatch between a frontend-focused candidate and a security/backend role. The Candidate's verdict of 0/100 is defensible on the strict reading that this is not an Angular frontend role, but the Agent's 26/100 reflects a more nuanced assessment that the candidate brings relevant seniority, domain expertise, and engineering culture fit despite the fundamental stack mismatch—a position that stands up to its own logic better than the absolute rejection.
- `ats:Westwing:westwing:2725399` (agent 0 vs her 50, diff 50) -- *she_is_outlier*: The agent's logic is sound: the job explicitly requires React, React Router SSR, and GraphQL, while the candidate is Angular-primary with no demonstrated React or GraphQL experience. The agent appropriately weighted the critical tech stack mismatch heavily, making a 0/100 defensible given the highest-weighted dimension fails entirely, whereas the candidate's 50/100 seems to overlook or minimize this fundamental requirement gap.
- `ats:codecentric:codecentric:2576418` (agent 26 vs her 0, diff 26) -- *weak_rationale*: The Agent's scoring is internally contradictory—assigning 0 points for 'no Angular mention' while simultaneously giving 2+2+3 points in other categories, yet arriving at 26/100. The Agent acknowledges multiple mismatches (seniority scope 'off-domain', role type mismatch, culture signals unrelated to resume) but still scores it substantially higher than the Candidate's clear rejection, suggesting the Agent is inflating a score for a role that objectively doesn't fit the candidate profile rather than defending a coherent scoring framework.
- `ats:neoshare:neoshare:2449961` (agent 48 vs her 92, diff 44) -- *weak_rationale*: The Agent's own analysis undermines their low score: they identify a strong skill match, exceeding seniority, aligned culture, and only two gaps (niche tech and German fluency). These gaps are material but don't justify a 48/100 when the candidate demonstrably meets ~80% of core requirements; a 48 suggests disqualification, not a qualified-but-cautious reject. The Candidate's 92 overweights location and partial matches, but is closer to the technical reality than the Agent's contradictory reasoning.
- `ats:neoshare:neoshare:2477965` (agent 62 vs her 91, diff 29) -- *she_is_outlier*: The Agent's rationale is internally consistent and well-reasoned, acknowledging strong stack fit, domain alignment, and even explicitly addressing location concerns by noting no German requirement exists. The Candidate's dismissal based solely on 'Not in germany based' appears to contradict the Agent's finding that the JD has no German requirement and allows remote work, making her verdict the unusual call despite the significant score gap.
- `ats:neoshare:neoshare:2752998` (agent 56 vs her 92, diff 36) -- *she_is_outlier*: The Agent's rationale is internally sound and methodical—the skill matches are genuine but incomplete (missing Nx, Microfrontends, TailwindCSS, Chart.js), and the German language requirement is an explicit hard-miss (A2 vs. fluency required). The Candidate's 92/100 relies on 'most skills match' and location, which glosses over both the missing stack components and the material language gap that the JD flags as essential, making her verdict the unusual call despite the superficial appeal of location-based scoring.
- `synthetic:junior-exact-stack` (agent 30 vs her 61, diff 31) -- *she_is_outlier*: The agent's rationale is internally sound and well-evidenced: the candidate is a 9+ year senior with team leadership and architecture ownership applying for an explicit 0-2 year entry-level role with documented hard blockers (seniority band and salary floor of €48-55k vs €65k minimum). The candidate's one-line 'Junior Role and I would like a Senior' actually aligns with the agent's seniority mismatch concern but contradicts their own 61/100 score, making their verdict the unusual outlier rather than the agent's.
