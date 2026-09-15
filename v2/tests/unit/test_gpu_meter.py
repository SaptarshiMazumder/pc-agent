"""The meter: rented minutes become charges on the account, exactly once, at the row's rate; a
short charge stops the machine; no credits means no rental; a host with no port pays itself."""

import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from vast.application.instance_settings import InstanceSettings
from vast.application.interfaces.account_charges import ChargeOutcome
from vast.application.services.gpu_meter import MIN_SLICE_S, GpuMeter
from vast.application.services.instance_reaper import InstanceReaper
from vast.application.services.instance_service import InstanceService
from vast.domain.errors import BudgetExhausted
from vast.domain.instance import MachineInstance, label_for
from vast.infrastructure.sql_instance_store import SqlInstanceStore
from vast.infrastructure.sqlite_schema import create_schema
from vast.presentation.vast_router import _view


class Charges:
    """A recording AccountCharges: covers everything until `credits_left` runs out."""

    def __init__(self, credits_left: int = 10**9, per_usd: float = 166_667.0):
        self.credits_left = credits_left
        self.per_usd = per_usd
        self.calls: list[dict] = []

    def charge(self, account_id, usd, *, event_id, label, agent_id=""):
        credits = int(round(usd * self.per_usd))
        self.calls.append({"account": account_id, "usd": usd, "event_id": event_id, "label": label,
                           "agent_id": agent_id, "credits": credits})
        taken = min(credits, self.credits_left)
        self.credits_left -= taken
        return ChargeOutcome(covered=taken == credits, credits=taken, shortfall=credits - taken)

    def funded(self, account_id, agent_id=""):
        return self.credits_left > 0


@pytest.fixture
def rig(tmp_path):
    path = tmp_path / "vast.sqlite"

    @contextmanager
    def db():
        connection = sqlite3.connect(path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    store = SqlInstanceStore()
    with db() as c:
        create_schema(c)
    clock = [1000.0]
    charges = Charges()
    settings = InstanceSettings(credit_markup=1.0)
    market = Mock()
    market.configured = True
    market.list_instances.return_value = []
    market.destroy.return_value = None
    probe = Mock()
    probe.busy.return_value = True  # working: the idle sweep must never be the reason it dies
    meter = GpuMeter(db=db, store=store, charges=charges, settings=settings, now=lambda: clock[0])
    reaper = InstanceReaper(db=db, store=store, marketplace=lambda: market, settings=settings,
                            now=lambda: clock[0], probe=probe, meter=meter)
    service = InstanceService(db=db, store=store, marketplace=lambda: market, settings=settings,
                              now=lambda: clock[0], probe=probe, charges=charges)

    def rent(account="alice", hourly=0.6, agent="comfy-artchitect", instance_id=123):
        with db() as c:
            row, _ = store.claim(c, account, clock[0], agent_id=agent)
            store.mark_running(c, row.id, instance_id=instance_id, machine_id=1, hourly_usd=hourly,
                               now=clock[0], auth_token="tok")
            store.mark_ready(c, row.id, url="http://gpu", now=clock[0])
            return store.by_id(c, row.id)

    return SimpleNamespace(db=db, store=store, clock=clock, charges=charges, meter=meter,
                           reaper=reaper, service=service, market=market, rent=rent)


def row(rig, row_id):
    with rig.db() as c:
        return rig.store.by_id(c, row_id)


def test_a_live_machine_is_charged_for_the_minutes_since_the_last_sweep(rig):
    r = rig.rent(hourly=0.6)
    rig.clock[0] += 600  # ten minutes
    result = {"errors": []}
    assert rig.meter.charge_due(result) == []
    assert len(rig.charges.calls) == 1
    call = rig.charges.calls[0]
    assert call["account"] == "alice" and call["agent_id"] == "comfy-artchitect"
    assert call["usd"] == pytest.approx(0.6 * 600 / 3600)
    assert call["event_id"] == f"gpu:{r.id}:{int(rig.clock[0])}" and call["label"] == "gpu"
    assert result["charged"] == 1 and result["charged_usd"] == pytest.approx(0.1)
    assert row(rig, r.id).billed_until == rig.clock[0]


def test_a_second_sweep_charges_only_what_is_new(rig):
    rig.rent(hourly=0.6)
    rig.clock[0] += 600
    rig.meter.charge_due({"errors": []})
    rig.clock[0] += 60
    rig.meter.charge_due({"errors": []})
    assert [round(c["usd"], 6) for c in rig.charges.calls] == [0.1, 0.01]


def test_a_slice_shorter_than_the_minimum_waits_for_the_next_sweep(rig):
    rig.rent(hourly=0.6)
    rig.clock[0] += MIN_SLICE_S / 2
    rig.meter.charge_due({"errors": []})
    assert rig.charges.calls == []
    rig.clock[0] += MIN_SLICE_S
    rig.meter.charge_due({"errors": []})
    assert len(rig.charges.calls) == 1
    assert rig.charges.calls[0]["usd"] == pytest.approx(0.6 * (1.5 * MIN_SLICE_S) / 3600)


def test_a_dead_machine_is_charged_up_to_its_death_and_then_never_again(rig):
    r = rig.rent(hourly=0.6)
    rig.clock[0] += 300
    with rig.db() as c:
        rig.store.mark_dead(c, r.id, reason="released", now=rig.clock[0])
    rig.clock[0] += 3600  # an hour later, the sweep finally runs
    rig.meter.charge_due({"errors": []})
    assert len(rig.charges.calls) == 1 and rig.charges.calls[0]["usd"] == pytest.approx(0.05)
    rig.meter.charge_due({"errors": []})
    assert len(rig.charges.calls) == 1


def test_the_markup_scales_the_rate(rig):
    rig.rent(hourly=1.0)
    rig.meter._settings = InstanceSettings(credit_markup=1.3)
    rig.clock[0] += 3600
    rig.meter.charge_due({"errors": []})
    assert rig.charges.calls[0]["usd"] == pytest.approx(1.3)


def test_a_machine_whose_account_ran_dry_is_stopped_by_the_sweep_with_the_reason(rig):
    r = rig.rent(hourly=0.6)
    rig.market.list_instances.return_value = [MachineInstance(
        instance_id=123, label=label_for(r.id), status="running", machine_id=1,
        hourly_usd=0.6, url="http://gpu",
    )]
    rig.charges.credits_left = 1  # one credit: the first slice cannot be covered
    rig.clock[0] += 600
    result = rig.reaper.sweep()
    assert result["unfunded"] == 1 and result["unfunded_stopped"] == 1
    rig.market.destroy.assert_called_once_with(123)
    dead = row(rig, r.id)
    assert dead.state == "dead" and dead.dead_reason == "out of credits"
    # The slice was still charged (the minutes were used), and marked paid.
    assert dead.billed_until == rig.clock[0]


def test_a_charge_that_raises_costs_that_slice_and_nothing_more(rig):
    r = rig.rent(hourly=0.6)
    rig.clock[0] += 600
    rig.charges.charge = Mock(side_effect=RuntimeError("ledger down"))
    result = {"errors": []}
    rig.meter.charge_due(result)
    assert result["errors"] and "ledger down" in result["errors"][0]
    # Marked before the charge: the failed slice is not charged again later (under-charge,
    # never double-charge).
    assert row(rig, r.id).billed_until == rig.clock[0]


def test_no_credits_means_no_rental(rig):
    rig.charges.credits_left = 0
    with pytest.raises(BudgetExhausted):
        rig.service.ensure("alice", agent_id="comfy-artchitect")
    rig.market.search_offers.assert_not_called()


def test_without_a_port_nothing_is_charged_and_rentals_are_not_gated(rig):
    meter = GpuMeter(db=rig.db, store=rig.store, charges=None, settings=InstanceSettings(),
                     now=lambda: rig.clock[0])
    rig.rent(hourly=0.6)
    rig.clock[0] += 600
    result = {"errors": []}
    assert meter.charge_due(result) == [] and result["charged"] == 0


def test_the_view_tells_the_account_the_openable_link_and_the_hourly_credits(rig):
    r = rig.rent(hourly=0.6)
    view = _view(r, credits_per_hour=lambda hourly: int(round(hourly * 166_667)))
    assert view["open_url"] == "http://gpu/?token=tok"
    assert view["credits_per_hour"] == 100_000
    assert _view(None)["open_url"] == ""
