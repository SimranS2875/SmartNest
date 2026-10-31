# SmartNest — Assignments 1.2, 2.1, 2.2, 3.1 & 3.2

SmartNest is an autonomous Home Planning and Product Selection Assistant. This repository contains the backend foundation, a rule-based environmental reasoner (Assignment 1.2), concurrent background worker infrastructure (Assignment 2.1), distributed agent messaging over TCP sockets (Assignment 2.2), multi-agent coordination using Contract Net Protocol (Assignment 3.1), and explicit mathematical utility optimization for product selection (Assignment 3.2).

## Project Structure

```
backend/
├── src/
│   ├── main.py                              # Assignments 1.2 & 2.1 simulation entry point
│   ├── agent_b_server.py                    # Assignment 2.2: Agent B server (Terminal 1)
│   ├── agent_a_client.py                    # Assignment 2.2: Agent A client (Terminal 2)
│   ├── cnp_run.py                           # Assignment 3.1: CNP negotiation demo
│   ├── utility_optimization_run.py          # Assignment 3.2: utility optimization demo
│   └── smartnest/
│       ├── core/
│       │   ├── models.py                    # Pydantic models (events, logs, message envelope)
│       │   └── events.py                    # Thread-safe EventStream (queue.Queue)
│       ├── agents/
│       │   ├── base_agent.py                # Abstract BaseAgent
│       │   └── reflex_agent.py              # ReflexAgent (condition → action)
│       ├── reasoning/
│       │   ├── rule_engine.py               # Generic, reusable RuleEngine + Rule
│       │   └── rules.py                     # SmartNest domain rules (9 rules)
│       ├── simulation/
│       │   ├── environment.py               # MockEnvironment emitting simulated events
│       │   └── event_stream.py              # EventStream re-export
│       ├── workers/
│       │   ├── background_worker.py         # Reusable daemon worker abstraction
│       │   ├── product_monitor_worker.py    # Monitors product prices & availability
│       │   ├── budget_monitor_worker.py    # Monitors budget thresholds
│       │   ├── worker_manager.py            # Centralized lifecycle management
│       │   ├── catalog_provider.py          # Product catalogue abstraction
│       │   └── metrics.py                   # Thread-safe performance metrics
│       ├── cnp/                             # Assignment 3.1: Contract Net Protocol (see below)
│       ├── optimization/                    # Assignment 3.2: utility model and optimizer (see below)
│       └── communication/
│           ├── message_codec.py             # JSON serialization for MessageEnvelope
│           ├── socket_transport.py          # TCP transport with length-prefixed framing
│           ├── message_router.py            # Dispatch messages to registered handlers
│           ├── remote_agent_server.py       # TCP server accepting connections
│           └── task_handlers.py             # STATE_SYNC, TASK_REQUEST handlers + evaluators
├── tests/
│   ├── conftest.py
│   ├── test_models.py                       # Model serialization, validation, message types
│   ├── test_rule_engine.py                  # Engine mechanics (priority, enable, errors)
│   ├── test_rules.py                        # Each rule: positive, negative, malformed
│   ├── test_agent.py                        # ReflexAgent end-to-end scenarios
│   ├── test_environment.py                 # MockEnvironment & EventStream
│   ├── test_background_worker.py            # Worker lifecycle, status, exceptions, metrics
│   ├── test_product_monitor_worker.py       # Price/availability change detection
│   ├── test_budget_monitor_worker.py        # Budget threshold & exceeded detection
│   ├── test_worker_manager.py              # Registration, concurrent start/stop, CSV export
│   ├── test_message_codec.py               # Encode/decode, invalid JSON, type validation
│   ├── test_socket_transport.py            # Framing, partial reads, errors, context manager
│   ├── test_message_router.py              # Handler dispatch, unsupported types, exceptions
│   ├── test_task_handlers.py               # STATE_SYNC, TASK_REQUEST, evaluate_furniture_fit
│   ├── test_server_integration.py          # Full client-server round-trip over TCP
│   ├── test_negotiation_state.py, test_proposal_evaluator.py,
│   │   test_product_catalog.py, test_cnp_integration.py      # Assignment 3.1
│   └── test_utility_model.py, test_constraint_validator.py,
│       test_utility_optimizer.py, test_utility_cnp_integration.py  # Assignment 3.2
├── pyproject.toml
├── .env.example
└── README.md
```

## Setup

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate     # macOS/Linux
pip install -e ".[dev]"
```

## Running the Simulation (Assignments 1.2 & 2.1)

```bash
cd backend
python src/main.py
```

## Two-Terminal Demonstration (Assignment 2.2)

### Terminal 1 — Agent B Server

```bash
cd backend
python src/agent_b_server.py
```

The server listens on `127.0.0.1:8765` and waits for Agent A to connect.

### Terminal 2 — Agent A Client

```bash
cd backend
python src/agent_a_client.py
```

Agent A connects to Agent B, transfers apartment configuration, receives an ACK, sends a furniture evaluation task request, and displays the result.

### What Happens

1. **State Sync**: Agent A sends its apartment state (project_id, budget, rooms, preferences) to Agent B. Agent B validates it, stores it locally, and returns an ACK.

2. **Task Request**: Agent A sends a `TASK_REQUEST` with a sofa product (width 230cm, price ₹24,999) to be evaluated against the living room (width 210cm).

3. **Autonomous Evaluation**: Agent B independently evaluates the furniture fit using the synchronized state — it does not hardcode the result. It checks:
   - Width: 230cm > 210cm → sofa exceeds room width by 20cm
   - Budget: ₹24,999 ≤ ₹30,000 → within budget
   - Result: `fits: false`, `recommendation: "Reject SOFA_001..."`

4. **Response**: Agent B returns an `INFORM_RESULT` message with the evaluation result. Task IDs correlate request to response.

### Configurable Options

```bash
python src/agent_b_server.py --host 127.0.0.1 --port 8765
python src/agent_a_client.py --host 127.0.0.1 --port 8765
```

## Running Tests

```bash
cd backend
python -m pytest
```

469 tests covering all modules (302 from Assignments 1.2–3.1, 167 for Assignment 3.2).

## Architecture

### Data Models (`core/models.py`)

All data structures use Pydantic v2 for validation and JSON serialization:

- **`EnvironmentalEvent`** — Structured observation with `event_id`, `event_type`, `source`, `timestamp`, `payload`. Describes the SmartNest environment.
- **`ActionLog`** — Records what rule fired and the result of its action.
- **`MessageEnvelope`** — Inter-agent communication with `sender`, `receiver`, `message_type`, `task_id`, `payload`, `timestamp`, `schema_version`, `signature`. Separate from `EnvironmentalEvent`.

Message types include:
- Basic: `INFORM`, `REQUEST`, `QUERY`
- Assignment 2.2: `STATE_SYNC`, `TASK_REQUEST`, `ACK`
- Contract Net Protocol: `CFP`, `PROPOSAL`, `REJECT_PROPOSAL`, `ACCEPT_PROPOSAL`, `AWARD`, `INFORM_DONE`, `INFORM_RESULT`
- Failure: `FAILURE`, `CANCEL`

### Communication Layer (`communication/`)

#### MessageCodec (`message_codec.py`)

Serializes `MessageEnvelope` to/from JSON bytes. Handles invalid JSON, malformed payloads, and missing required fields with `CodecError`.

#### SocketTransport (`socket_transport.py`)

TCP client with length-prefixed framing (4-byte big-endian length + payload). Features:
- Reliable message send/receive with partial read handling
- Configurable timeouts: a timeout before any byte of a frame arrives raises the retryable `TransportTimeoutError`; once a header has arrived, the rest of the frame uses a separate, longer `frame_timeout`
- Oversized message rejection (`MAX_MESSAGE_SIZE = 16 MiB`)
- Connection close detection
- Context manager support

#### MessageRouter (`message_router.py`)

Dispatches `MessageEnvelope` objects to registered handlers by `MessageType`. Returns `FAILURE` for unsupported types. Handler exceptions are caught and returned as `FAILURE` rather than crashing the server.

#### RemoteAgentServer (`remote_agent_server.py`)

TCP server that:
- Listens on a configurable host/port
- Accepts connections in a background daemon thread
- Handles each client in its own thread
- Reads framed messages, routes them, sends responses
- Replies `FAILURE` (`malformed_message`) to a frame that isn't a valid `MessageEnvelope` and keeps the connection open
- Keeps idle connections open (optional `idle_timeout` to drop silent clients)
- `port` reports the OS-assigned port when started with `port=0`
- Graceful shutdown via `threading.Event`; `stop()` also closes client sockets so it returns promptly

#### TaskHandlers (`task_handlers.py`)

- **`TaskHandlerRegistry`** — Holds synchronized apartment state and registered task evaluators. Evaluators receive a deep copy, so they can't modify the stored state.
- **`handle_state_sync`** — Validates `project_id` and the shape of `rooms` (a mapping of room → numeric `width_cm`/`length_cm`), stores the state, returns ACK, or FAILURE with `invalid_rooms`
- **`handle_task_request`** — Dispatches to registered evaluators, returns INFORM_RESULT
- **`evaluate_furniture_fit`** — Checks product width vs room width, depth vs room length (when both are given), and price vs remaining budget. An unknown `target_room` returns `error: unknown_target_room` rather than silently using another room. Results are data-dependent, not hardcoded.

### EventStream vs MessageEnvelope

These two remain strictly separate:
- **`EventStream`** carries `EnvironmentalEvent` objects (observations about the environment)
- **`MessageEnvelope`** carries inter-agent messages over TCP sockets

Workers continue publishing to `EventStream` without modification. The socket transport operates exclusively on `MessageEnvelope` objects. Future integration between the two would use an explicit adapter, not schema mixing.

### Rule Engine (`reasoning/`)

9 rules evaluated in priority order. See Assignment 1.2 documentation in the code.

### Background Workers (`workers/`)

Concurrent daemon threads monitoring product prices, availability (including products removed from the catalogue), and budget. See Assignment 2.1 documentation in the code.

Metric definitions:
- **Detection latency** — time from the moment the monitored source changed (providers record change timestamps) to the moment the event was published. With a 1 s poll interval it averages around 0.5 s.
- **Poll errors** — polls that raised an exception. The worker keeps running (status stays `RUNNING`) and exposes the exception as `last_error`; status becomes `ERROR` only if the loop itself crashes.
- **Events lost** — events that could not be published (e.g. a bounded `EventStream` stayed full past the publish timeout).
- A `stop()` that times out leaves the status at `STOPPING` and refuses to restart until the old thread has exited, so two polling loops can never run at once.

### Agent (`agents/`)

- **`BaseAgent`** — Abstract base with `agent_id`, `name`, `state`, `action_log`, `logger`.
- **`ReflexAgent`** — Delegates event evaluation to a `RuleEngine`. No internal memory — pure reflex.

## Future Compatibility

- **Assignment 3.1 (Multi-agent)**: Implemented — see below. It reuses the router, server and transport unchanged.
- **Assignment 3.2 (Utility optimization)**: Implemented — see below. The socket transport and task handlers did not need changes.
- **Assignment 4.1 (BDI)**: Agent B's received state is accessible through `TaskHandlerRegistry.apartment_state`. Future BDI architecture will manage beliefs separately.
- **Assignment 4.2 (Real APIs)**: External product research will use separate provider interfaces, not the communication layer.
- **Assignment 5.1 (Security)**: `MessageEnvelope.signature` field is an Ed25519 placeholder. No crypto implemented yet.
- **Assignment 5.2 (FastAPI)**: `RemoteAgentServer` runs in daemon threads and doesn't block HTTP request handling.

## Assignment 3.1: Multi-Agent Coordination (Contract Net Protocol)

### CNP Package (`cnp/`)

- **`negotiation_state.py`** — State machine with 10 states (CREATED → CFP_SENT → COLLECTING_PROPOSALS → EVALUATING → NEGOTIATING → AWARDED → EXECUTING → COMPLETED → TERMINATED, with FAILED as alternative). AWARDED → NEGOTIATING is allowed so a refused award can go to the runner-up. Invalid transitions raise `InvalidStateTransition`. All transitions are timestamped in `history`.
- **`proposal_evaluator.py`** — Weighted utility scoring isolated from the negotiation protocol (see below). Ready for Assignment 3.2 to replace with a more sophisticated optimizer.
- **`product_catalog.py`** — Thread-safe simulated catalogues for two independent bidding agents, 3 sofas each. All entries are clearly simulated — no real retailer URLs.
- **`coordinator_agent.py`** — Owns the negotiation policy. `run_negotiation()` runs CFP rounds, gives bidders constraint and offer feedback, decides when to stop, awards the best offer from any round (falling back to the runner-up if the winner refuses), and notifies everyone.
- **`bidding_agent.py`** — Independent product research agent with its own `RemoteAgentServer` and catalogue. Picks a product for the constraints announced so far, concedes on price when it is not leading (each agent has its own maximum discount), keeps separate state per negotiation, and checks an award against its own offers (negotiation ID, task ID, product, price) before reporting INFORM_DONE.
- **`cnp_demo.py`** — Sets up the scenario and prints the round-by-round summary, timeline and state history.

### Running the CNP Demonstration

```bash
cd backend
python src/cnp_run.py
```

### CNP Negotiation Lifecycle

1. **CFP (Round 1)** — The coordinator announces what the buyer wants: a three-seater minimalist sofa, budget ₹30,000. The 210 cm room width is the coordinator's private site knowledge and is **not** announced yet (`reveal_constraints_progressively=True`; set it to `False` to announce everything up front).
2. **Proposals** — Each agent independently picks a product from its own catalogue. Without a width, each offers its best-value sofa.
3. **Evaluation** — The coordinator applies hard constraints and utility scoring.
4. **Constraint feedback** — Proposals rejected for a constraint the bidders weren't told about cause the next CFP to reveal it (`updated_constraints: {"max_width": 210}`). Bidders then switch to products that fit.
5. **Offer feedback** — Once a feasible offer exists, each bidder is told whether it is leading and asked to improve. Trailing bidders concede on price (2.5% per round, up to their own limit); the leader holds.
6. **Stopping rule** — At least `min_rounds` (2) rounds always run. After that the coordinator stops when the best offer stops improving, when there is nothing new to reveal and no feasible offer, or at `max_rounds`.
7. **Award** — The best offer from any round gets an AWARD (product + price). If the winner refuses, the next-best offer is tried.
8. **Execution & consensus** — The winner verifies the award against its own offers and sends INFORM_DONE. Losers get REJECT_PROPOSAL, everyone gets INFORM_RESULT, and the negotiation terminates. With no feasible offer, everyone gets CANCEL and the state goes FAILED → TERMINATED.

CFPs go to every bidder before any reply is read, so bidders work in parallel. A bidder that disconnects is dropped and the negotiation continues with the others.

### Utility Calculation (3.1 scoring — still the CNP default)

| Component | Weight | Formula |
|-----------|--------|---------|
| Spatial | 0.35 | `product_width / room_width` |
| Price | 0.30 | `1.0 - (proposed_price / max_budget)` |
| Style | 0.20 | exact match = 1.0, partial (one name contains the other) = 0.5, otherwise or missing style = 0.0; no preference = 1.0 |
| Availability | 0.15 | in_stock = 1.0, low_stock = 0.7, backorder/preorder = 0.4 |

Hard constraints reject before scoring: not purchasable (out of stock / unavailable / discontinued), price over budget, unknown width, width over room width, too few seats. Weights are validated (known keys, non-negative, positive sum) and normalised to sum to 1. Ties are broken by lower price, then earlier round.

### Sample Negotiation (actual output of `python src/cnp_run.py`)

```
ROUND 1   (width not announced)
   research_agent_a   SOFA_A1   230 cm  ₹24,999          REJECTED: Width 230 cm exceeds room width 210 cm
   research_agent_b   SOFA_B1   225 cm  ₹22,999          REJECTED: Width 225 cm exceeds room width 210 cm

ROUND 2   (CFP reveals max_width = 210)
   research_agent_a   SOFA_A3   205 cm  ₹29,499          utility 0.6967
   research_agent_b   SOFA_B2   200 cm  ₹28,499          utility 0.6983   ← leading

ROUND 3   (offer feedback: A trailing, B leading)
   research_agent_a   SOFA_A3   205 cm  ₹28,762 (-2.5%)  utility 0.7040   ← new leader
   research_agent_b   SOFA_B2   200 cm  ₹28,499          utility 0.6983

[AWARD]      → research_agent_a: SOFA_A3 at ₹28,762 (round 3)
[EXECUTION]  research_agent_a → INFORM_DONE
[REJECT]     → research_agent_b
[CONSENSUS]  → both agents: INFORM_RESULT
Negotiation successfully terminated.
```

### Tests

CNP tests:
- `test_negotiation_state.py` — transitions, invalid transitions, history, award-refusal path
- `test_proposal_evaluator.py` — hard constraints (incl. seating, missing width), scoring, style/availability rules, weight validation, ranking tie-breaks
- `test_product_catalog.py` — construction, filtering, unknown width, availability states, independence, no URLs
- `test_cnp_integration.py` — over real TCP: round-1 width rejections, constraint reveal and product switching, concessions and discount limits, the coordinator's stopping rule, best-across-rounds award, refusal fallback, bidder drop-out, no-proposal failure, timeline labels/timestamps, ID correlation, award verification

## Assignment 3.2: Self-Interested Agent Utility Optimization

### Running the Demonstration

```bash
cd backend
python src/utility_optimization_run.py
```

It evaluates 14 simulated sofas from Agent A's and Agent B's catalogues, prints every rejection with its reason, the normalised component scores, the ranking, the selected product with its substituted calculation, each agent's own best option, a hand-checkable example, boundary probes, and finally runs the same decision through the Contract Net Protocol over TCP. Nothing in the output is hardcoded: the winner and ranking are computed from the data.

### Utility Function

```
U(p) = 100 × (0.30·P + 0.25·Q + 0.20·F + 0.15·S + 0.10·C)        U ∈ [0, 100]

P  price affordability   clamp(1 − price / budget, 0, 1)
Q  quality               0.80 × rating/5 + 0.20 × min(review_count/100, 1)
F  spatial utilisation   0.50 × width/max_width + 0.50 × depth/max_depth
S  style compatibility   1.0 exact · 0.5 recognised-compatible · 0.0 otherwise
C  colour compatibility  1.0 exact · 0.5 same colour family · 0.0 otherwise
```

All five components are normalised to [0, 1]. Weights, sub-weights (0.80/0.20, 0.50/0.50), the review saturation (100), the match scores and the style/colour tables are all in `ScoringConfig` and can be changed. A configuration is validated when it is created: the five weights must be finite, non-negative and sum to exactly 1 (as must each pair of sub-weights), and strings or booleans used as numbers are refused. A bad weight vector raises `ScoringConfigError` before it can reach the optimizer; `ScoringConfig.normalised()` turns relative importances into valid weights.

**Spatial score assumptions.** The limits are the free space available for the sofa (wall length × floor depth). A sofa that uses more of that space without exceeding it fills the area better and scores higher, up to 1.0 at an exact fit; width and depth matter equally by default. F is only defined for products within the limits: hard constraints are checked first, and the scorer refuses an oversized product instead of returning a value above 1. Walkway clearance is the user's choice: they enter the clearance-adjusted space as the limit.

**Style and colour.** Deterministic lookup tables (e.g. minimalist ↔ scandinavian / contemporary / modern / japandi; neutral ↔ beige / cream / grey / white / ivory / taupe / charcoal). An unknown style or colour scores 0. No natural-language interpretation or LLM is involved.

### Hard Constraints

A candidate is eligible only if **price ≤ budget**, **width ≤ max width**, **depth ≤ max depth**, **seats ≥ minimum** and **availability is confirmed** (`in_stock` or `low_stock`, configurable). Limits are inclusive. Every violated constraint is reported (e.g. `price ₹30,001 exceeds budget ₹30,000 by ₹1`). Utility is computed only after a candidate passes, so a high score can never override a constraint.

Before the constraints are checked, each record is validated. A missing or malformed mandatory field (missing dimensions, negative or zero price or size, rating outside 0–5, NaN or infinite values, non-integer review counts, empty style or colour, non-mapping records, duplicates) makes the record **invalid** with an explicit reason. It is never given a default value. Invalid requirements (e.g. a zero budget, which would divide by zero) raise `InvalidRequirementsError`.

### Architecture (`smartnest/optimization/`)

| Module | Responsibility |
|---|---|
| `scoring_config.py` | `ScoringConfig`: validated, JSON-serialisable weights and compatibility tables |
| `constraint_validator.py` | `UserRequirements`, `ProductCandidate`, `validate_record`, `ConstraintValidator` (record validation and hard constraints with reasons) |
| `utility_model.py` | `UtilityModel`: the five components, the weighted total and a `UtilityBreakdown` (components, weights, points contributed) |
| `utility_optimizer.py` | `UtilityOptimizer`: exhaustive evaluation, ranking, selection, explanation; `optimize_async` for non-blocking use |

The package has no dependency on CNP, sockets, databases, LLMs or the UI. Every result is a Pydantic model that round-trips through JSON.

**Algorithm.** Exhaustive and deterministic, which is exact for catalogue sizes like these: inspect every candidate → validate → check hard constraints → score each eligible one → sort by **U ↓, Q ↓, price ↑, product ID ↑, source agent ↑** → select rank 1. The result reports how many candidates were inspected, eligible, rejected and invalid, every breakdown, the ranking, the selection, an explanation of how it beat the runner-up (component by component, including tie-break rules), and `verified_optimal`, an explicit check that the selection's U is ≥ every feasible U. With no feasible candidate, `selected` is `None`: there is no recommendation.

### CNP Integration

- `ProposalEvaluator()` is unchanged (3.1 scoring is still the default, so all 3.1 behaviour and tests are preserved). `ProposalEvaluator.utility_based()` (or `ProposalEvaluator(utility_model=UtilityModel(...))`) delegates `evaluate()` to the optimizer. `utility_score` is U/100, so both modes share the [0, 1] scale, and the full 0–100 breakdown is in `utility_breakdown`. `rank()` uses the same tie-break order as the optimizer.
- **The coordinator always recalculates.** It scores the product attributes at the **offered** price (so concessions are re-scored) and attributes the offer to the bidder it is actually talking to. A bidder's `self_reported_utility` is never used for ranking. It is kept in `reported_utility`, and a mismatch is logged as a `utility_discrepancy` event.
- **Bidders evaluate their own catalogues.** `BiddingAgent(..., utility_optimizer=UtilityOptimizer())` proposes its own highest-utility feasible product whenever the CFP states every constraint the model needs, and attaches its own estimate and breakdown at the offered price. `evaluate_own_catalog()` returns the agent's full optimisation result. If the width hasn't been announced yet (round 1 with progressive disclosure), the agent falls back to its 3.1 heuristic.
- `CoordinatorAgent.set_requirements()` accepts optional `room_depth` and `preferred_colour`.
- Negotiation protocol, awards, discount policies and consensus handling are unchanged.

### Simulated Candidates

`catalog_agent_a()` / `catalog_agent_b()` still return the three sofas each that the 3.1 scenario uses. `extended=True` adds four more per agent (14 in total), chosen to exercise each constraint and its boundary. All products now carry depth, rating, review count and source agent. All data is simulated: no real listings, prices, reviews or URLs.

### Sample Output (`python src/utility_optimization_run.py`)

Requirements: budget ₹30,000, width ≤ 210 cm, depth ≤ 110 cm, ≥ 3 seats, minimalist, neutral.

```
Rank  ID           Price        P       Q       F       S       C         U
   1  SOFA_A2    ₹27,499   0.0834  0.8960  0.8825  1.0000  1.0000     67.55
   2  SOFA_B3    ₹26,999   0.1000  0.7940  0.8524  1.0000  1.0000     64.90
   3  SOFA_A3    ₹29,499   0.0167  0.9520  0.9335  1.0000  0.5000     62.97
   4  SOFA_B2    ₹28,499   0.0500  0.8880  0.9307  1.0000  0.5000     62.32
   5  SOFA_A6    ₹30,000   0.0000  0.8560  1.0000  1.0000  0.5000     61.40
   6  SOFA_B4    ₹27,999   0.0667  0.9580  0.9725  0.5000  0.5000     57.90
   7  SOFA_A7    ₹26,499   0.1167  0.8400  0.9426  0.0000  0.0000     43.35

Rejected:
  SOFA_A1  width 230 cm exceeds maximum width 210 cm by 20 cm
  SOFA_A4  depth 115 cm exceeds maximum depth 110 cm by 5 cm
  SOFA_A5  seating capacity 2 is below the minimum 3
  SOFA_B1  width 225 cm exceeds maximum width 210 cm by 15 cm
  SOFA_B5  price ₹30,001 exceeds budget ₹30,000 by ₹1
  SOFA_B6  availability not confirmed (out_of_stock)
  SOFA_B7  width 211 cm exceeds maximum width 210 cm by 1 cm

★ SOFA_A2 — Compact Minimalist Sofa A2 (research_agent_a) at ₹27,499
  U = 100 × (0.30×0.0834 + 0.25×0.8960 + 0.20×0.8825 + 0.15×1.0000 + 0.10×1.0000)
    = 2.50 + 22.40 + 17.65 + 15.00 + 10.00 = 67.55
  Beats runner-up SOFA_B3 (64.90) by 2.65: quality +2.55, spatial +0.60; trails on price −0.50.
  verified_optimal = True

CNP over TCP (utility mode): R1 A1/B1 rejected for width → R2 A2 67.55 vs B3 64.90
  → R3 B concedes to ₹26,324 (65.57) → winner SOFA_A2, same as the exhaustive optimizer.
```

**Hand-checkable example** (also run by the demo and the tests): budget ₹10,000, room 200 × 100 cm.
- X: ₹5,000, 200 × 100, rating 5 / 100 reviews, exact style and colour → P = 0.5, Q = F = S = C = 1 → U = 100(0.15 + 0.25 + 0.20 + 0.15 + 0.10) = **85.00**.
- Y: ₹2,000, 100 × 50, rating 4, compatible style, other colour → U = 100(0.24 + 0.21 + 0.10 + 0.075) = **62.50**.
- Z: cheapest and top-rated but 1 cm too wide → **rejected**.

Selected: X.

### Tests

The 3.2 tests run without network access or external services and give the same result on every run:
- `test_utility_model.py`: each component against its formula, clamping, rating/dimension/budget guards, weight and config validation, determinism, serialisation
- `test_constraint_validator.py`: every boundary (= limit eligible, +1 rejected), unavailable states, all malformed-field cases, zero/negative/NaN requirements
- `test_utility_optimizer.py`: the 14-candidate scenario checked against an independent formula implementation, the hand-checked example, empty / all-infeasible / all-invalid sets, duplicates, tie-breaking, input-order independence, repeated runs, async, JSON round-trip, the demo
- `test_utility_cnp_integration.py`: evaluator modes, re-scoring at the offered price, ignored and audited self-reports, a lying bidder, bidder self-evaluation, full TCP negotiations that must agree with the exhaustive optimizer

### Design Notes for Later Assignments

- **4.1 (BDI):** The optimizer is a pure function of (candidates, requirements, config) with no agent state, so a BDI agent can call it while choosing intentions (beliefs → `UserRequirements`, options → candidates) without touching the scoring logic.
- **4.2 (Real APIs):** Evaluation depends only on the record schema (`validate_record` / `ProductCandidate`), not on where products come from. An API-backed catalogue only has to produce compatible dicts, and incomplete API data is rejected as invalid, never defaulted.
- **5.1 (Signing):** No security logic was added. `MessageEnvelope.signature` is untouched.
- **5.2 (FastAPI):** `UtilityOptimizer.optimize_async()` runs in a worker thread, so the event loop stays free.
- **Gemini / MongoDB:** An LLM may later turn natural language into `UserRequirements`, but all scores are computed by Python. Every model is a JSON-serialisable Pydantic model, ready for document storage.
