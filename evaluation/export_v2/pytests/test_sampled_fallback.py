"""The sampled Step 2 B3 fallback costing: policy-general, and the three rules that make sampling honest."""
from fractions import Fraction
import json

import pytest

from export_v2 import sampled_fallback as sf


EPS = Fraction(1, 1000)


def census(pairs):
    """pairs: (numerator, denominator, multiplicity, sign-bit occurrences)."""
    turns = {Fraction(n, d): m for n, d, m, _ in pairs}
    signs = {Fraction(n, d): s for n, d, _, s in pairs}
    return turns, signs


def spread_census(count, *, multiplicity=1, denominator=1 << 20):
    turns = {Fraction(2 * i + 1, denominator): multiplicity for i in range(count)}
    return turns, {t: 0 for t in turns}


def flat_worker(value=Fraction(100)):
    def worker(job):
        turn, _eps = job
        return turn, (f"word({turn})",), Fraction(0), (value, value)
    return worker


def ramp_worker(step=Fraction(1), base=Fraction(100)):
    """Expected T grows with the turn's numerator: a non-constant stratum with a bounded span."""
    def worker(job):
        turn, _eps = job
        v = base + step * (turn.numerator % 7)
        return turn, (f"word({turn})",), Fraction(0), (v, v)
    return worker


def contract(**over):
    c = dict(sf.load_contract())
    c.update(over)
    return c


# ---------------------------------------------------------------- the contract


def test_contract_ships_with_the_module_and_validates():
    c = sf.load_contract()
    assert c["schema"] == sf.SCHEMA
    assert len(bytes.fromhex(c["salt_hex"])) == 32
    assert Fraction(c["range_T"]) == 48
    assert int(c["sample_total"]) == 16_000 and float(c["budget_seconds"]) == 600.0


@pytest.mark.parametrize("bad", [{"schema": "other/v1"}, {"salt_hex": "00"}, {"sample_total": 1},
                                 {"conf": 0.9}, {"range_T": "0"}, {"round_size": 0}, {"budget_seconds": 0}])
def test_invalid_contract_is_refused(tmp_path, bad):
    path = tmp_path / "c.json"
    path.write_text(json.dumps(contract(**bad)))
    with pytest.raises(ValueError, match="invalid sampled-costing contract"):
        sf.load_contract(path)


# ---------------------------------------------------------------- policy-generality


def test_the_sample_comes_from_the_policys_own_census_not_a_frozen_bank():
    """Two policies with different angle inventories are sampled differently, and nothing names a fixture."""
    a = spread_census(40)
    b = spread_census(40, denominator=1 << 21)
    c = contract(sample_total=8, round_size=8)
    pa, _ = sf.price_step2_budgeted(*a, EPS, c, budget_seconds=None, workers=1, worker=flat_worker())
    pb, _ = sf.price_step2_budgeted(*b, EPS, c, budget_seconds=None, workers=1, worker=flat_worker())
    assert pa.inventory_sha256 != pb.inventory_sha256
    assert pa.sample_sha256 != pb.sample_sha256
    assert sf.inventory_digest(*a) == sf.inventory_digest(*a)     # a census, not a seed


def test_multiplicity_and_sign_bits_change_the_stratification():
    turns, signs = spread_census(30)
    signs = {t: (1 if i % 2 else 0) for i, t in enumerate(sorted(turns))}
    keys = set(sf.select_sample(turns, signs, salt=b"\x00" * 32, total=30)[1])
    assert len(keys) == 2 and {k[0] for k in keys} == {"none", "all"}


def test_eighth_turns_are_exact_and_never_sampled():
    turns, signs = census([(1, 8, 3, 0), (3, 8, 2, 0), (1, 1 << 20, 5, 0), (3, 1 << 20, 7, 0)])
    sample, populations, exact = sf.select_sample(turns, signs, salt=b"\x01" * 32, total=2)
    assert set(exact) == {Fraction(1, 8), Fraction(3, 8)}
    assert sum(populations.values()) == 2
    assert all(Fraction(1, 8) not in v for v in sample.values())


def test_a_full_census_carries_no_sampling_uncertainty():
    turns, signs = spread_census(6)
    price, run = sf.price_step2_budgeted(turns, signs, EPS, contract(sample_total=6, round_size=6),
                                         budget_seconds=None, workers=1, worker=ramp_worker())
    assert run["cap_reached"] and price.n_sampled == 6
    assert price.clt_halfwidth == 0.0 and price.eb_halfwidth == 0.0
    assert price.failure_fraction_upper == 0.0


# ---------------------------------------------------------------- RULE 1: a range excess is out of contract


def test_observed_range_excess_is_refused_and_never_silently_widened():
    turns, signs = spread_census(40)
    c = contract(sample_total=8, round_size=8, range_T="1")
    with pytest.raises(sf.OutOfContractRefusal) as err:
        sf.price_step2_budgeted(turns, signs, EPS, c, budget_seconds=None, workers=1,
                                worker=ramp_worker(step=Fraction(10)))
    assert "out of contract" in str(err.value) and "not rankable" in str(err.value)


def test_the_diagnostic_prices_an_excess_but_marks_it_non_rankable():
    turns, signs = spread_census(40)
    c = contract(sample_total=8, round_size=8, range_T="1")
    price, _ = sf.price_step2_budgeted(turns, signs, EPS, c, budget_seconds=None, workers=1,
                                       worker=ramp_worker(step=Fraction(10)), allow_out_of_contract=True)
    assert price.out_of_contract and price.observed_span_T > price.range_T


def test_a_range_excess_emits_no_success_receipt():
    turns, signs = spread_census(40)
    c = contract(sample_total=8, round_size=8, range_T="1")
    with pytest.raises(sf.OutOfContractRefusal):
        sf.cost_policy_fallback(turns, signs, EPS, worker=ramp_worker(step=Fraction(10)), contract=c, workers=1)


def test_the_range_bound_sizes_multiplicity_times_per_turn_expected_T():
    values = {Fraction(1, 3): Fraction(10), Fraction(2, 3): Fraction(14)}
    assert sf.range_bound(values, 5, 5, Fraction(48)) == 5 * 48               # constant multiplicity: n * W
    assert sf.range_bound(values, 5, 9, Fraction(48)) == 9 * 48 + 4 * 10      # n_hi*W + (n_hi - n_lo)*obs_min
    assert sf.range_bound(values, 1, 1, Fraction(2)) == 4                     # observed span wins when wider


# ---------------------------------------------------------------- RULE 2: a resource stop is INCOMPLETE


def test_a_blown_budget_is_incomplete_not_a_refusal_and_not_a_pass():
    turns, signs = spread_census(40)
    c = contract(sample_total=40, round_size=4, budget_seconds=1)
    ticks = iter([0.0] + [float(i) for i in range(1, 200)])
    with pytest.raises(sf.ResourceStop) as err:
        sf.cost_policy_fallback(turns, signs, EPS, worker=flat_worker(), contract=c, workers=1,
                                clock=lambda: next(ticks))
    assert err.value.verdict == "INCOMPLETE"
    assert not isinstance(err.value, (sf.OutOfContractRefusal, sf.CertificationRefusal))
    assert "incomplete" in str(err.value).lower()


def test_an_in_budget_round_stop_is_a_valid_prefix_and_still_scores():
    turns, signs = spread_census(40)
    c = contract(sample_total=40, round_size=4, budget_seconds=100)
    # start, began(1), end(1), check, began(2), end(2), check -> round 3 (1.25 * 50 s) will not fit, elapsed
    ticks = iter([0.0, 0.0, 30.0, 30.0, 30.0, 80.0, 80.0, 80.0])
    receipt = sf.cost_policy_fallback(turns, signs, EPS, worker=ramp_worker(), contract=c, workers=1,
                                      clock=lambda: next(ticks))
    assert receipt["verdict"] == "ACCEPTED"
    assert not receipt["run"]["cap_reached"] and receipt["run"]["rounds"] < 10
    assert 0 < receipt["interval"]["n_sampled"] < 40


def test_a_resource_stop_is_distinguished_from_a_missing_certified_word():
    turns, signs = spread_census(12)
    c = contract(sample_total=12, round_size=12)

    def missing(job):
        raise ValueError("no word within the allowance")

    with pytest.raises(sf.CertificationRefusal, match="missing certified word"):
        sf.cost_policy_fallback(turns, signs, EPS, worker=missing, contract=c, workers=1)


def test_a_worker_failure_is_infrastructure_not_a_refusal():
    turns, signs = spread_census(12)

    def dies(job):
        raise MemoryError("worker died")

    with pytest.raises(sf.SamplingInfrastructureError, match="worker failure"):
        sf.cost_policy_fallback(turns, signs, EPS, worker=dies, contract=contract(sample_total=12, round_size=12),
                                workers=1)


def test_an_invalid_worker_count_is_a_configuration_error():
    turns, signs = spread_census(12)
    with pytest.raises(sf.SamplingConfigurationError, match="1..16"):
        sf.cost_policy_fallback(turns, signs, EPS, worker=flat_worker(), contract=contract(), workers=0)


# ---------------------------------------------------------------- RULE 3: the receipt carries the provenance


def test_the_receipt_records_samples_inventory_interval_bound_and_the_pricing_code():
    turns, signs = spread_census(40)
    c = contract(sample_total=12, round_size=12)
    receipt = sf.cost_policy_fallback(turns, signs, EPS, worker=ramp_worker(), contract=c, workers=1)
    interval, provenance = receipt["interval"], receipt["provenance"]
    assert interval["n_sampled"] == 12 and interval["n_cap"] == 12
    assert Fraction(interval["ranked_upper_T"]) > 0
    assert interval["clt_halfwidth"] > 0 and interval["eb_halfwidth"] > 0
    assert interval["basis"] == sf.RANKED_BASIS
    assert provenance["inventory_sha256"] == sf.inventory_digest(turns, signs).hex()
    assert provenance["sample_sha256"] and provenance["checked_words_sha256"]
    assert provenance["contract_sha256"] == sf.contract_digest(c)
    assert provenance["range_T"] == "48" and provenance["observed_span_T"] is not None
    assert provenance["priced_by"]["entry"] == "export_v2.sampled_fallback.cost_policy_fallback"
    assert provenance["priced_by"]["sha256"] == sf.pricer_fingerprint()
    assert receipt["rankable"] is True


def test_the_receipt_binding_is_what_a_retry_must_reproduce():
    turns, signs = spread_census(40)
    c = contract(sample_total=12, round_size=12)
    one = sf.cost_policy_fallback(turns, signs, EPS, worker=ramp_worker(), contract=c, workers=1)
    two = sf.cost_policy_fallback(turns, signs, EPS, worker=ramp_worker(), contract=c, workers=1)
    assert sf.binding(one) == sf.binding(two)
    assert set(sf.RECEIPT_BINDING) <= set(one["provenance"])


def test_an_interval_without_provenance_cannot_be_built():
    """Every receipt field the rules require is present or the helper refuses to emit one."""
    turns, signs = spread_census(12)
    receipt = sf.cost_policy_fallback(turns, signs, EPS, worker=ramp_worker(),
                                      contract=contract(sample_total=12, round_size=12), workers=1)
    with pytest.raises(ValueError, match="receipt is missing"):
        sf.check_receipt({k: v for k, v in receipt.items() if k != "provenance"})
    sf.check_receipt(receipt)


def test_the_failure_fraction_is_clamped_and_labelled():
    turns, signs = spread_census(40)
    price, _ = sf.price_step2_budgeted(turns, signs, EPS, contract(sample_total=2, round_size=2),
                                       budget_seconds=None, workers=1, worker=ramp_worker())
    assert price.failure_fraction_upper == 1.0
    assert "not a circuit-level failure probability" in sf.FAILURE_BASIS


def test_the_turn_work_envelope_is_bounded():
    turns = {Fraction(1, 3): 1, Fraction(3, 2): 1}
    with pytest.raises(ValueError, match="canonical Fraction"):
        sf.select_sample(turns, {}, salt=b"\x00" * 32, total=2)
