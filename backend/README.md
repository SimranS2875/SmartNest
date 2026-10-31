# SmartNest — Assignments 1.2, 2.1, 2.2 & 3.1

SmartNest is an autonomous Home Planning and Product Selection Assistant. This repository contains the backend foundation, a rule-based environmental reasoner (Assignment 1.2), concurrent background worker infrastructure (Assignment 2.1), distributed agent messaging over TCP sockets (Assignment 2.2), and multi-agent coordination using Contract Net Protocol (Assignment 3.1).

## Project Structure

```
backend/
├── src/
│   ├── main.py                              # Assignments 1.2 & 2.1 simulation entry point
│   ├── agent_b_server.py                    # Assignment 2.2: Agent B server (Terminal 1)
│   ├── agent_a_client.py                    # Assignment 2.2: Agent A client (Terminal 2)
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
│   └── test_server_integration.py          # Full client-server round-trip over TCP
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

302 tests covering all modules.

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
- **Assignment 3.2 (Utility optimization)**: Socket transport and task handlers are independent of mathematical utility evaluation.
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

### Utility Calculation

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

302 total tests. CNP tests:
- `test_negotiation_state.py` — transitions, invalid transitions, history, award-refusal path
- `test_proposal_evaluator.py` — hard constraints (incl. seating, missing width), scoring, style/availability rules, weight validation, ranking tie-breaks
- `test_product_catalog.py` — construction, filtering, unknown width, availability states, independence, no URLs
- `test_cnp_integration.py` — over real TCP: round-1 width rejections, constraint reveal and product switching, concessions and discount limits, the coordinator's stopping rule, best-across-rounds award, refusal fallback, bidder drop-out, no-proposal failure, timeline labels/timestamps, ID correlation, award verification
