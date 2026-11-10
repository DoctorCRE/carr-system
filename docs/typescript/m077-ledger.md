# M077 monthly cash ledger

Card: WR622.S1.J742

## System design

The runway module owns pure, bounded monthly schedules and Decimal cash accumulation. The vertical module owns sizing and building checklists. Existing Zod schemas own public input and result types. Tool dispatch remains the validation and refusal interface. No external calls, persistent storage, queues, cache, writes, or deployment changes occur here. Availability, circuit breakers, idempotency, consistency across requests and N+1 access do not apply. Each invocation is independent. Capacity is bounded by the existing schema limits of 120 months, 120 entries per schedule and 25 collection weights. At ten times request volume the caller needs ten times CPU; this conversion adds no fan-out or shared state.

The intended end state has one TypeScript implementation for each calculator, with explicit imports and no JavaScript compatibility wrappers. The public schemas and rounding policy remain unchanged. Internal sizing is a discriminated configuration union; public inputs and outputs use schema inference.

## Ledger statechart

[Mermaid source](m077-ledger.mmd) was written before adding characterization tests or converting either module. It describes observable ledger outcomes, not intermediate writes. Injections, expenses and receipts combine atomically into each month's closing balance. Reserve breach and exhaustion report the first qualifying month even after recovery. Initial cash below reserve reports month zero. Missing costs and omitted schedules independently make the model incomplete.

The statechart helper generated a TLA+ skeleton but could not check or render it because TLC is unavailable. No formal verification or PNG rendering is claimed. Reproduce the attempted generation outside the repository with `node /Users/booko/.claude/plugins/marketplaces/cursor-plugins/pstack/skills/statechart/scripts/statechart.mjs from-chart /tmp/j742-statechart/ledger.mmd --out /tmp/j742-statechart/render --png` after copying the Mermaid source there. This chart abstracts numeric amounts, collection weights, calendar arithmetic and the first-breach history into states. Executed calculator tests establish those details.

| Transition | Test name |
| --- | --- |
| Incompatible input to refusal | A1 case 5: rejects negative cash, bad lag weights and dual rent modes |
| Initial cash to scheduled monthly ledger | A1 case 1: exact cash ledger and reserve funding |
| Delay opening, retain calendar costs, shift receipts | A1 case 2: delayed receipts keep fixed-calendar costs; zero is not exhaustion |
| Apply cash receipts or collected billings | A1 case 3: collection fraction and lag convolution |
| Apply equity funding | runway ledger: scheduled injections, reserve breach, exhaustion and recovery |
| Apply loan funding and debt expenses | A1 case 4: zero-rate debt and annual rentable-SF rent |
| Pay fixed and opening-relative expenses | runway: exact decimal accumulation, payroll, shifted expense and incomplete horizon |
| Closing cash falls below reserve | runway ledger: scheduled injections, reserve breach, exhaustion and recovery |
| Closing cash falls below zero | runway ledger: scheduled injections, reserve breach, exhaustion and recovery |
| Later receipts restore cash | runway ledger: scheduled injections, reserve breach, exhaustion and recovery |
| Reach complete horizon, including breached/exhausted paths | runway ledger: scheduled injections, reserve breach, exhaustion and recovery |
| Reach incomplete horizon with omitted schedule | runway: funded loan stays in the ledger when first payment is beyond the horizon |
| Reach incomplete horizon with missing costs | runway ledger: missing cost categories suppress funding recommendations |
| Initial reserve breach and Decimal rounding | runway ledger: initial reserve breach and half-up output rounding |

Characterization ran against the unchanged JavaScript implementations on the origin/main base before conversion. Both existing suites remain JavaScript and keep their names, so M104 has no partial TypeScript consumption and no duration entry is renamed.
