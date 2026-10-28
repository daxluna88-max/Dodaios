# ruff: noqa: E501  (long Italian customer messages; no formatter was run)
"""Provider-neutral inference/voice/vision BROKER interface around the budget ledger (ADR-0068, block 20).

Written statically and NOT RUN. Pure Python: no network, no provider SDK, no provider key, no price
list, no wiring into the running app. The real transport (HTTP front door on the node) and the
provider adapters (which hold the master keys, node side only) are NOT in this module.

Rules encoded here
* The customer VM ("guest") only ever holds an OPAQUE endpoint and token (``GuestCredential``).
  The broker stores only a hash of the token. No provider master key exists in, or can be reached
  from, this module's guest-facing types; ``forbidden_env_names`` is the guard used on the guest
  environment before it is handed over.
* Every paid AI/voice/vision call goes ``authorize`` (identity -> engine allowed -> caps on tokens,
  steps, images, voice seconds, retries, concurrency -> WORST-CASE cost ceiling from an OWNER-SET
  price table -> ledger ``reserve``) and only then ``settle`` (measured usage) or ``release`` (the
  call provably never started). Unknown price is never zero: refused, or an explicit owner estimate.
* The customer is never charged above what was reserved. Usage above the reservation, late provider
  records and calls nobody reserved are OPERATOR_LOSS: booked under the operator, not under the
  customer, never shown to the customer as debt, and they stop new work for that customer and flag
  reconciliation. The ledger is therefore not a mirror of the provider invoice and no text may
  promise an absolute provider-side hard cap (not proven).
* Broker down means NO inference: the guest gateway has no fallback path to any provider.

Honest limits: in-memory bookkeeping (open calls, in-flight counters, turn counters, escalation
usage, operator-loss totals, holds) is lost on restart; the ledger (SQLite) is the durable truth, so
after a restart ``recover`` turns dangling reservations into held-``unknown`` rows. A multi-node
deployment needs these tables in the same transactional database as the ledger.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from dodaios.cloud.budget_ledger import MESSAGES_IT, BudgetLedger, period_of

USAGE_COUNTER_MAX = 10**12  # sanity bound for one call's measured token/image/second counters
OPERATOR_CUSTOMER = "__operator__"  # pseudo customer that carries OPERATOR_LOSS rows


class CostClass(StrEnum):
    INFERENCE = "inference"
    VOICE = "voice"
    VISION = "vision"  # screenshots / computer-use images
    COMPUTE = "compute"
    STORAGE = "storage"
    EGRESS = "egress"
    BACKUP = "backup"
    OPERATOR_LOSS = "operator_loss"  # never reservable, never charged to a customer


#: Broker class -> ledger class. Vision shares the AI bucket, backup shares storage; the broker
#: enforces their finer limits itself (images per call/turn, escalation allowance).
LEDGER_CLASS: dict[CostClass, str] = {
    CostClass.INFERENCE: "ai_inference",
    CostClass.VOICE: "voice",
    CostClass.VISION: "ai_inference",
    CostClass.COMPUTE: "compute",
    CostClass.STORAGE: "storage",
    CostClass.EGRESS: "egress",
    CostClass.BACKUP: "storage",
}
CALL_CLASSES = (CostClass.INFERENCE, CostClass.VOICE, CostClass.VISION)
ACCRUAL_CLASSES = (CostClass.COMPUTE, CostClass.STORAGE, CostClass.EGRESS, CostClass.BACKUP)


class EngineTier(StrEnum):
    ECONOMY = "economy"  # default, cheap model
    ESCALATED = "escalated"  # stronger model; needs an explicit escalation allowance


class StopAction(StrEnum):
    NONE = "none"
    REFUSE_CALL = "refuse_call"  # this call only; the turn may go on with something else
    STOP_TURN = "stop_turn"  # end the current turn/job
    STOP_NEW_WORK = "stop_new_work"  # start nothing new for this customer until resolved


#: Customer-visible text (Italian first). No word of debt, no promise of an absolute cap.
BROKER_MESSAGES_IT: dict[str, str] = {
    "unauthenticated": "Sessione non valida. Accedi di nuovo.",
    "engine_not_allowed": "Questo motore AI non e incluso nel tuo piano: non e stato usato.",
    "escalation_not_allowed": "Per questa operazione serve un motore piu potente, ma non hai attivato l'extra: non e stata eseguita.",
    "limit_tokens": "La richiesta supera la dimensione massima consentita: non e stata eseguita.",
    "limit_steps": "Questo lavoro ha raggiunto il numero massimo di passaggi ed e stato fermato.",
    "limit_images": "Questo lavoro ha raggiunto il numero massimo di schermate analizzate ed e stato fermato.",
    "limit_voice": "La durata massima della voce e stata raggiunta: la voce si ferma qui.",
    "limit_retries": "Troppi tentativi per questo passaggio: Jarvis si ferma qui.",
    "limit_concurrency": "Jarvis sta gia facendo il massimo di operazioni in parallelo: riprova tra poco.",
    "operator_hold": "Jarvis e fermo per una verifica dei conti da parte nostra. Non ti viene chiesto nulla.",
    "broker_down": "Il servizio di calcolo non risponde: Jarvis non puo ragionare adesso e non spende nulla. Riprova piu tardi.",
    "invalid": "Richiesta non valida: non e stata eseguita.",
    "conflict": "Questa richiesta e gia stata registrata con dati diversi: non e stata eseguita.",
    "replay_closed": "Questa richiesta e gia conclusa: non viene eseguita di nuovo.",
}


def message_it(code: str) -> str:
    return BROKER_MESSAGES_IT.get(code) or MESSAGES_IT.get(code, "")


_QUOTA_CODES = {"no_cap", "cap_reached", "class_cap_reached", "customer_blocked", "breaker", "global_cap", "operator_hold"}
_TURN_CODES = {"scope_ceiling", "call_ceiling", "limit_steps", "limit_images", "limit_voice", "limit_retries", "limit_tokens"}


def stop_action_for(code: str) -> StopAction:
    """What the agent must do after a refusal code. Quota/hold/breaker stop NEW work too."""
    if code in _QUOTA_CODES:
        return StopAction.STOP_NEW_WORK
    if code in _TURN_CODES:
        return StopAction.STOP_TURN
    return StopAction.REFUSE_CALL


def _int(value: object, *, minimum: int = 0) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return None
    return value


# -- owner-set price table -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Price:
    """Integer minor units. ``None`` = unknown (NOT zero). Rates per million tokens, per image, per
    second of voice. The owner fills it from a provider's published price; nothing is built in."""

    per_m_input: int | None = None
    per_m_output: int | None = None
    per_image: int | None = None
    per_voice_second: int | None = None


class OwnerPriceTable:
    """Returns 'unknown' (``None``) unless the account owner set the price explicitly (audited)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._prices: dict[str, Price] = {}
        self._estimates: dict[tuple[str, CostClass], int] = {}
        self.audit: list[tuple[str, str, str]] = []

    def set_price(self, engine: str, price: Price, *, actor: str) -> None:
        for value in (price.per_m_input, price.per_m_output, price.per_image, price.per_voice_second):
            if value is not None and _int(value) is None:
                raise ValueError("price components must be non-negative integers or None")
        if not engine or not actor:
            raise ValueError("engine and actor are required")
        with self._lock:
            self._prices[engine] = price
            self.audit.append((actor, "set_price", engine))

    def set_call_estimate(self, engine: str, cls: CostClass, amount: int, *, actor: str) -> None:
        """Explicit OWNER estimate of one call's worst case, used only when the price is unknown.
        Reported as an estimate everywhere; never a default."""
        if _int(amount) is None or not engine or not actor or cls not in CALL_CLASSES:
            raise ValueError("valid engine, class, non-negative integer amount and actor required")
        with self._lock:
            self._estimates[(engine, cls)] = amount
            self.audit.append((actor, "set_call_estimate", f"{engine}/{cls.value}={amount}"))

    def price(self, engine: str) -> Price | None:
        with self._lock:
            return self._prices.get(engine)

    def estimate(self, engine: str, cls: CostClass) -> int | None:
        with self._lock:
            return self._estimates.get((engine, cls))


def _i(entry: Mapping[str, object], key: str, default: int = 0) -> int:
    value = entry.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _ceil_rate(units: int, rate: int, per: int = 1) -> int:
    return -((-units * rate) // per)


# -- identity: opaque guest credential -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Principal:
    customer: str
    session: str


@dataclass(frozen=True, slots=True)
class GuestCredential:
    """The ONLY thing the customer VM receives: an opaque endpoint and an opaque token."""

    endpoint: str
    token: str


_FORBIDDEN_FRAGMENTS = ("API_KEY", "APIKEY", "SECRET", "MASTER", "PRIVATE_KEY", "ACCESS_KEY", "PASSWORD")
_FORBIDDEN_VALUE_PREFIXES = ("sk-", "sk_", "AKIA", "AIza", "xox", "ghp_")


def guest_environment(cred: GuestCredential) -> dict[str, str]:
    """The complete provider-related environment of the guest: broker endpoint and broker token."""
    return {"JARVIS_BROKER_ENDPOINT": cred.endpoint, "JARVIS_BROKER_TOKEN": cred.token}


def forbidden_env_names(env: Mapping[str, str]) -> list[str]:
    """Names in a guest environment that look like a provider key. Empty = nothing suspicious.
    Heuristic guard, not a proof: the real control is that keys are never on the guest at all."""
    bad = []
    for name, value in env.items():
        if name in ("JARVIS_BROKER_ENDPOINT", "JARVIS_BROKER_TOKEN"):
            continue
        upper = name.upper()
        if any(f in upper for f in _FORBIDDEN_FRAGMENTS) or str(value).startswith(_FORBIDDEN_VALUE_PREFIXES):
            bad.append(name)
    return sorted(bad)


class GuestCredentialIssuer:
    """Issues and resolves opaque tokens. Only a SHA-256 of the token is stored."""

    def __init__(self, endpoint: str) -> None:
        self._endpoint = endpoint
        self._lock = threading.Lock()
        self._rows: dict[str, tuple[Principal, float, bool]] = {}

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def issue(self, customer: str, session: str, *, ttl_s: float, now: float | None = None) -> GuestCredential:
        if not customer or not session or ttl_s <= 0:
            raise ValueError("customer, session and a positive ttl are required")
        now = time.time() if now is None else now
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._rows[self._hash(token)] = (Principal(customer, session), now + ttl_s, False)
        return GuestCredential(self._endpoint, token)

    def resolve(self, token: str, *, now: float | None = None) -> Principal | None:
        now = time.time() if now is None else now
        with self._lock:
            row = self._rows.get(self._hash(token or ""))
        if row is None or row[2] or row[1] <= now:
            return None
        return row[0]

    def revoke_customer(self, customer: str) -> int:
        with self._lock:
            hits = [h for h, (p, _e, rev) in self._rows.items() if p.customer == customer and not rev]
            for h in hits:
                p, e, _ = self._rows[h]
                self._rows[h] = (p, e, True)
        return len(hits)


# -- entitlements and limits -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Grant:
    """What a customer may use. ``engines`` maps engine id -> tier. ``escalation_allowance`` (minor
    units per period) is the only budget stronger models and vision/computer-use may draw on."""

    engines: Mapping[str, EngineTier] = field(default_factory=dict)
    escalation_allowance: int = 0


@dataclass(frozen=True, slots=True)
class BrokerLimits:
    """Owner-set bounds. The defaults are conservative PLACEHOLDERS (counts, not prices)."""

    max_input_tokens: int = 32_000
    max_output_tokens: int = 2_000
    max_steps_per_turn: int = 12
    max_images_per_call: int = 1
    max_images_per_turn: int = 6
    max_retries: int = 1  # extra attempts per step (each attempt is reserved separately)
    max_concurrency: int = 2  # paid calls in flight per customer
    max_voice_seconds_per_segment: int = 30
    max_voice_seconds_per_session: int = 600
    per_step_ceiling: int | None = None  # None = ledger call ceiling only
    per_turn_ceiling: int = 0  # amount per turn scope; 0 = nothing can be reserved (fail closed)
    operator_loss_breaker_threshold: int | None = None  # trip the GLOBAL breaker at this period loss


@dataclass(frozen=True, slots=True)
class CallSpec:
    """One paid call, as the guest asks for it. All bounds are worst-case inputs."""

    cls: CostClass
    engine: str
    turn_id: str
    step: int
    request_id: str  # unique per attempt; the same id again is an idempotent replay
    max_input_tokens: int = 0
    max_output_tokens: int = 0
    images: int = 0
    voice_seconds: int = 0


@dataclass(frozen=True, slots=True)
class Usage:
    """Measured usage reported by the node-side provider adapter (never by the guest)."""

    input_tokens: int = 0
    output_tokens: int = 0
    images: int = 0
    voice_seconds: int = 0
    provider_cost: int | None = None  # provider-reported amount, minor units, when available


def worst_case(spec: CallSpec, price: Price | None) -> int | None:
    """Cost ceiling of one call, or ``None`` if any needed component has an unknown price."""
    if price is None:
        return None
    total = 0
    needs = (
        (spec.max_input_tokens, price.per_m_input, 1_000_000),
        (spec.max_output_tokens, price.per_m_output, 1_000_000),
        (spec.images, price.per_image, 1),
        (spec.voice_seconds, price.per_voice_second, 1),
    )
    for units, rate, per in needs:
        if units > 0:
            if rate is None:
                return None
            total += _ceil_rate(units, rate, per)
    return total


@dataclass(frozen=True, slots=True)
class Authorization:
    ok: bool
    code: str
    res_id: str = ""
    amount: int = 0  # the worst-case ceiling held
    price_basis: str = ""  # known | owner_estimate
    remaining: int | None = None
    message_it: str = ""
    stop: StopAction = StopAction.NONE


@dataclass(frozen=True, slots=True)
class SettleResult:
    ok: bool
    code: str  # ok | replay | unknown_usage | conflict | not_found | invalid
    customer_amount: int | None = None
    operator_loss: int = 0


@dataclass(slots=True)
class _OpenCall:
    res_id: str
    customer: str
    session: str
    engine: str
    cls: CostClass
    period: str
    amount: int
    escalated: bool


@dataclass(slots=True)
class _Turn:
    steps: dict[int, set[str]] = field(default_factory=dict)
    images: int = 0


class OperatorLossBook:
    """Records OPERATOR_LOSS under a pseudo customer so it can never enter a customer's figures.
    The per-reservation totals live in the ledger (same transaction as the operator cost row), so a
    Broker re-created on the same ledger sees them and a failed write leaves nothing half-booked."""

    def __init__(self, ledger: BudgetLedger) -> None:
        self._ledger = ledger

    def record(self, res_id: str, customer: str, cls: CostClass, period: str, total: int, *, now: float | None = None) -> int:
        """Raise the loss of ``res_id`` to ``total`` (monotonic). Returns the newly added amount.
        Raises if the durable write fails: the caller must treat that as an unresolved loss."""
        if _int(total) is None or total == 0:
            return 0
        return self._ledger.raise_loss(res_id, customer, OPERATOR_CUSTOMER, LEDGER_CLASS.get(cls, "ai_inference"), period, total, now=now)

    def total_for(self, customer: str, period: str, ledger_cls: str) -> int:
        return sum(
            t for (_k, c, cls, per, t) in self._ledger.losses()
            if c == customer and per == period and cls == ledger_cls
        )

    def period_total(self, period: str) -> int:
        return sum(t for (_k, _c, _cls, per, t) in self._ledger.losses() if per == period)

    def records(self) -> list[tuple[str, str, str, str, int]]:
        return self._ledger.losses()


class Broker:
    def __init__(
        self,
        ledger: BudgetLedger,
        prices: OwnerPriceTable,
        issuer: GuestCredentialIssuer,
        *,
        limits: BrokerLimits | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.ledger, self.prices, self.issuer = ledger, prices, issuer
        self.limits = limits or BrokerLimits()
        self.losses = OperatorLossBook(ledger)
        self._clock = clock
        self._lock = threading.RLock()
        self._grants: dict[str, Grant] = {}
        self._open: dict[str, _OpenCall] = {}
        self._inflight: dict[str, set[str]] = {}
        self._turns: dict[tuple[str, str], _Turn] = {}
        self._voice: dict[tuple[str, str], int] = {}
        self._escalated: dict[tuple[str, str], int] = {}
        self._holds: dict[str, str] = {}
        self.needs_reconcile: set[str] = set()
        self.audit: list[tuple[str, str, str]] = []

    # -- configuration (explicit, audited) -------------------------------------------------------

    def set_grant(self, customer: str, grant: Grant, *, actor: str) -> None:
        if not customer or not actor or _int(grant.escalation_allowance) is None:
            raise ValueError("customer, actor and a non-negative integer allowance are required")
        with self._lock:
            self._grants[customer] = grant
            self.audit.append((actor, "set_grant", customer))

    def holds(self) -> dict[str, str]:
        with self._lock:
            return dict(self._holds)

    def clear_hold(self, customer: str, *, actor: str, note: str) -> bool:
        if not actor or not note:
            raise ValueError("clearing a hold needs an actor and a note")
        with self._lock:
            self.audit.append((actor, "clear_hold", f"{customer}: {note}"))
            self.needs_reconcile.discard(customer)
            return self._holds.pop(customer, None) is not None

    def _hold(self, customer: str, reason: str) -> None:
        with self._lock:
            self._holds.setdefault(customer, reason)
            self.needs_reconcile.add(customer)

    # -- authorize -------------------------------------------------------------------------------

    @staticmethod
    def _no(code: str, remaining: int | None = None) -> Authorization:
        return Authorization(False, code, remaining=remaining, message_it=message_it(code), stop=stop_action_for(code))

    def authorize(self, token: str, spec: CallSpec, *, now: float | None = None) -> Authorization:
        now = self._clock() if now is None else now
        who = self.issuer.resolve(token, now=now)
        if who is None:
            return self._no("unauthenticated")
        if (
            spec.cls not in CALL_CLASSES or not spec.engine or not spec.turn_id or not spec.request_id
            or any(_int(v) is None for v in (spec.step, spec.max_input_tokens, spec.max_output_tokens, spec.images, spec.voice_seconds))
        ):
            return self._no("invalid")
        with self._lock:  # check-then-register must be atomic (concurrency, steps, allowance)
            return self._authorize(who, spec, now)

    def _authorize(self, who: Principal, spec: CallSpec, now: float) -> Authorization:
        period = period_of(now)
        with self._lock:
            hold = who.customer in self._holds
            grant = self._grants.get(who.customer)
        if hold:
            return self._no("operator_hold")
        tier = None if grant is None else grant.engines.get(spec.engine)
        if tier is None:
            return self._no("engine_not_allowed")
        lim = self.limits
        if spec.max_input_tokens > lim.max_input_tokens or spec.max_output_tokens > lim.max_output_tokens:
            return self._no("limit_tokens")
        if spec.cls is CostClass.VISION and (spec.images < 1 or spec.images > lim.max_images_per_call):
            return self._no("limit_images")
        if spec.cls is not CostClass.VISION and spec.images:
            return self._no("invalid")
        if spec.cls is CostClass.VOICE and not 0 < spec.voice_seconds <= lim.max_voice_seconds_per_segment:
            return self._no("limit_voice")
        if spec.cls is not CostClass.VOICE and spec.voice_seconds:
            return self._no("invalid")

        amount, estimate = self._ceiling(spec)
        escalated = tier is EngineTier.ESCALATED or spec.cls is CostClass.VISION
        scope = f"turn:{who.customer}:{spec.turn_id}"
        idem = f"{who.customer}:{spec.turn_id}:{spec.request_id}"
        with self._lock:
            turn = self._turns.setdefault((who.customer, spec.turn_id), _Turn())
            attempts = turn.steps.get(spec.step)
            replay = attempts is not None and spec.request_id in attempts
            if not replay:
                if attempts is None and len(turn.steps) >= lim.max_steps_per_turn:
                    return self._no("limit_steps")
                if attempts is not None and len(attempts) > lim.max_retries:
                    return self._no("limit_retries")
                if spec.cls is CostClass.VISION and turn.images + spec.images > lim.max_images_per_turn:
                    return self._no("limit_images")
                if spec.cls is CostClass.VOICE and self._voice.get((who.customer, who.session), 0) + spec.voice_seconds > lim.max_voice_seconds_per_session:
                    return self._no("limit_voice")
                if len(self._inflight.get(who.customer, ())) >= lim.max_concurrency:
                    return self._no("limit_concurrency")
        effective = amount if amount is not None else estimate  # worst-case hold: known price OR approved estimate
        if effective is not None and lim.per_step_ceiling is not None and effective > lim.per_step_ceiling:
            return self._no("call_ceiling")
        if escalated:
            allow = 0 if grant is None else grant.escalation_allowance
            used = self._escalated.get((who.customer, period), 0)
            held = (amount if amount is not None else (estimate or 0))
            if allow <= 0:
                return self._no("escalation_not_allowed")
            if not replay and used + held > allow:
                return self._no("escalation_not_allowed")

        self.ledger.open_scope(
            scope, who.customer, max_amount=lim.per_turn_ceiling,
            max_calls=lim.max_steps_per_turn * (1 + lim.max_retries),
        )
        d = self.ledger.reserve(
            who.customer, LEDGER_CLASS[spec.cls], amount, idem=idem, period=period,
            scope=scope, owner_estimate=estimate if amount is None else None,
            fingerprint=self._fingerprint(who, spec), now=now,
        )
        if not d.ok:
            return Authorization(False, d.code, remaining=d.remaining,
                                 message_it=message_it(d.code), stop=stop_action_for(d.code))
        entry = self.ledger.get_entry(d.res_id) or {}
        if d.code == "replay" and entry.get("state") != "reserved":
            # The stored result is already settled/unknown/released: a replay returns that fact, it is
            # never permission to start another paid request with the same identity.
            return self._no("replay_closed", d.remaining)
        held_amount = _i(entry, "amount", amount or estimate or 0)
        with self._lock:
            turn.steps.setdefault(spec.step, set()).add(spec.request_id)
            if d.code != "replay":
                turn.images += spec.images
                if spec.cls is CostClass.VOICE:
                    key = (who.customer, who.session)
                    self._voice[key] = self._voice.get(key, 0) + spec.voice_seconds
                if escalated:
                    self._escalated[(who.customer, period)] = self._escalated.get((who.customer, period), 0) + held_amount
            self._inflight.setdefault(who.customer, set()).add(d.res_id)
            self._open.setdefault(d.res_id, _OpenCall(d.res_id, who.customer, who.session, spec.engine, spec.cls, period, held_amount, escalated))
        return Authorization(True, d.code, d.res_id, held_amount, str(entry.get("price_basis", "known")), d.remaining)

    @staticmethod
    def _fingerprint(who: Principal, spec: CallSpec) -> str:
        """Canonical identity of one authorized request; a replay must present the same one."""
        parts = (who.customer, who.session, spec.turn_id, spec.step, spec.cls.value, spec.engine, spec.request_id,
                 spec.max_input_tokens, spec.max_output_tokens, spec.images, spec.voice_seconds)
        return hashlib.sha256(repr(parts).encode()).hexdigest()

    def _ceiling(self, spec: CallSpec) -> tuple[int | None, int | None]:
        """(worst case from the owner price table | None, owner estimate | None)."""
        amount = worst_case(spec, self.prices.price(spec.engine))
        return amount, self.prices.estimate(spec.engine, spec.cls)

    # -- settle / release / late usage -----------------------------------------------------------

    def _done(self, res_id: str) -> _OpenCall | None:
        with self._lock:
            call = self._open.get(res_id)
            if call is not None:
                self._inflight.get(call.customer, set()).discard(res_id)
        return call

    @staticmethod
    def _valid_usage(usage: Usage) -> bool:
        counters = (usage.input_tokens, usage.output_tokens, usage.images, usage.voice_seconds)
        if any(_int(v) is None or v > USAGE_COUNTER_MAX for v in counters):
            return False
        return usage.provider_cost is None or _int(usage.provider_cost) is not None

    def actual_cost(self, call: _OpenCall | None, usage: Usage) -> int | None:
        if usage.provider_cost is not None:
            return _int(usage.provider_cost)
        if call is None:
            return None
        spec = CallSpec(call.cls, call.engine, "", 0, "", usage.input_tokens, usage.output_tokens, usage.images, usage.voice_seconds)
        return worst_case(spec, self.prices.price(call.engine))

    def settle(self, res_id: str, usage: Usage | None, *, measured: bool = True, now: float | None = None) -> SettleResult:
        """Replace the hold by the measured cost, capped at the reservation for the customer. Any
        excess is OPERATOR_LOSS. ``usage=None`` or an unpriced engine keeps the worst case held."""
        now = self._clock() if now is None else now
        entry = self.ledger.get_entry(res_id)
        if entry is None:
            return SettleResult(False, "not_found")
        call = self._done(res_id)
        if usage is not None and not self._valid_usage(usage):
            # Malformed measurement is not evidence of free execution: keep the worst case held
            # (as unknown) and require reconciliation.
            self.ledger.settle(res_id, None, now=now)
            with self._lock:
                self.needs_reconcile.add(str(entry["customer"]))
            return SettleResult(False, "invalid")
        actual = None if usage is None else self.actual_cost(call, usage)
        if actual is None:
            d = self.ledger.settle(res_id, None, now=now)
            return SettleResult(d.ok, d.code if d.ok else "conflict")
        reserved = _i(entry, "amount")
        part = min(actual, reserved)
        d = self.ledger.settle(res_id, part, measured=measured, now=now)
        if not d.ok:
            return SettleResult(False, d.code)
        loss = max(0, actual - part)
        if loss:  # idempotent per (res_id, total): a replay adds nothing
            self._operator_loss(res_id, str(entry["customer"]), call, str(entry["period"]), str(entry["cls"]), loss, "over_reservation", now)
        if call is not None and call.escalated and d.code != "replay":
            with self._lock:
                k = (call.customer, call.period)
                self._escalated[k] = max(0, self._escalated.get(k, 0) - reserved + part)
        return SettleResult(True, d.code, part, loss)

    def release(self, res_id: str, *, now: float | None = None) -> SettleResult:
        """The provider was provably never contacted: give the hold back."""
        call = self._done(res_id)
        d = self.ledger.release(res_id, now=now)
        if d.ok and d.code != "replay" and call is not None and call.escalated:  # refund only on the first transition
            with self._lock:
                k = (call.customer, call.period)
                self._escalated[k] = max(0, self._escalated.get(k, 0) - call.amount)
        return SettleResult(d.ok, d.code if d.ok else "conflict", 0 if d.ok else None)

    def report_late_usage(self, res_id: str, actual_total: int, *, now: float | None = None) -> SettleResult:
        """A provider record arrived after the reservation was released/settled lower/expired. The
        customer figure is NOT touched (unless the row is still held unknown: then it settles);
        the difference is OPERATOR_LOSS and stops new work for that customer."""
        now = self._clock() if now is None else now
        value = _int(actual_total)
        entry = self.ledger.get_entry(res_id)
        if value is None or entry is None:
            return SettleResult(False, "invalid" if value is None else "not_found")
        if entry["state"] in ("reserved", "unknown"):
            return self.settle(res_id, Usage(provider_cost=value), now=now)
        recorded = _i(entry, "actual") if entry["state"] == "settled" else 0
        loss = max(0, value - recorded)
        if loss:
            self._operator_loss(res_id, str(entry["customer"]), self._open.get(res_id), str(entry["period"]), str(entry["cls"]), loss, "late_usage", now)
        return SettleResult(True, "late_usage_recorded", recorded, loss)

    def report_unreserved_usage(self, customer: str, cls: CostClass, amount: int, *, idem: str, period: str, now: float | None = None) -> SettleResult:
        """Usage the provider reports for a call nobody reserved (bug, bypass, expired token)."""
        if cls not in CALL_CLASSES or _int(amount) is None or not customer or not idem:
            return SettleResult(False, "invalid")
        self._operator_loss(f"unreserved:{idem}", customer, None, period, LEDGER_CLASS[cls], amount, "unreserved", now, cls=cls)
        return SettleResult(True, "unreserved_recorded", 0, amount)

    def _operator_loss(self, res_id: str, customer: str, call: _OpenCall | None, period: str, ledger_cls: str, amount: int, reason: str, now: float | None, *, cls: CostClass | None = None) -> None:
        kind = cls or (call.cls if call is not None else next((c for c, v in LEDGER_CLASS.items() if v == ledger_cls), CostClass.INFERENCE))
        self._hold(customer, f"operator_loss:{reason}")  # fail closed first: a failed write below keeps the hold
        self.losses.record(res_id, customer, kind, period, amount, now=now)
        threshold = self.limits.operator_loss_breaker_threshold
        if threshold is not None and self.losses.period_total(period) >= threshold:
            self.ledger.trip_breaker(f"operator_loss_threshold:{period}", actor="broker", now=now)

    # -- accruals, recovery, reconciliation ------------------------------------------------------

    def record_accrual(self, customer: str, cls: CostClass, amount: int, *, idem: str, period: str, measured: bool = True) -> bool:
        """Compute/storage/egress/backup costs that accrue without a call (provider usage records)."""
        if cls not in ACCRUAL_CLASSES:
            return False
        return self.ledger.record_usage(customer, LEDGER_CLASS[cls], amount, idem=idem, period=period, measured=measured).ok

    def recover(self, *, now: float | None = None) -> int:
        """After a crash: expired reservations become held-unknown; in-memory counters restart empty."""
        with self._lock:
            self._inflight.clear()
        return self.ledger.recover(now=now)

    def reconcile(self, customer: str, period: str, cls: CostClass, provider_amount: int, *, tolerance: int = 0) -> str:
        """Compare with a provider figure for the customer. Operator loss already attributed to this
        customer is removed first (it is not in the customer's ledger by design)."""
        lcls = LEDGER_CLASS[cls]
        adjusted = max(0, provider_amount - self.losses.total_for(customer, period, lcls))
        result = self.ledger.reconcile(customer, period, lcls, adjusted, tolerance=tolerance)
        if result.action == "blocked":
            self._hold(customer, f"drift:{period}:{lcls}")
        return result.action

    def reconcile_global(self, period: str, cls: CostClass, provider_amount: int, *, tolerance: int = 0) -> str:
        return self.ledger.reconcile_global(period, LEDGER_CLASS[cls], provider_amount, tolerance=tolerance).action

    # -- customer view (never contains operator loss) --------------------------------------------

    def customer_usage_view(self, customer: str, period: str) -> dict[str, object]:
        u = self.ledger.usage(customer, period)
        with self._lock:
            hold = customer in self._holds
        return {
            "period": period,
            "classes": u["classes"],
            "cap": u["cap"],
            "remaining": u["remaining"],
            "no_overage": True,
            "auto_top_up": False,
            "stopped": bool(u["blocked"]) or hold,
            "message_it": message_it("operator_hold") if hold else "",
        }


# -- guest side: broker down means no inference ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class GatewayReply:
    ok: bool
    code: str
    message_it: str = ""
    stop: StopAction = StopAction.NONE
    payload: Mapping[str, object] = field(default_factory=dict)


class GuestGateway:
    """What agent code on the VM uses. It can reach ONLY the broker endpoint; any transport failure
    is a stop, never a fallback to a provider (there is no provider key on the VM to fall back to)."""

    def __init__(self, cred: GuestCredential, transport: Callable[[str, str, Mapping[str, object]], Mapping[str, object]]) -> None:
        self._cred, self._transport = cred, transport

    def call(self, request: Mapping[str, object]) -> GatewayReply:
        try:
            reply = self._transport(self._cred.endpoint, self._cred.token, request)
        except Exception:  # noqa: BLE001 - any failure of the broker path is the same outcome
            return GatewayReply(False, "broker_down", message_it("broker_down"), StopAction.STOP_TURN)
        if not isinstance(reply, Mapping) or "ok" not in reply:
            return GatewayReply(False, "broker_down", message_it("broker_down"), StopAction.STOP_TURN)
        code = str(reply.get("code", ""))
        try:
            stop = StopAction(str(reply.get("stop", "none")))
        except ValueError:
            stop = StopAction.STOP_TURN
        return GatewayReply(bool(reply["ok"]), code, str(reply.get("message_it", "")), stop, reply)
