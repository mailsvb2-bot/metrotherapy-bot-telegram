from services.messenger.entrypoints import parse_start_payload


def test_parse_start_payload_variants():
    assert parse_start_payload(None).kind == 'plain'
    assert parse_start_payload('ref_123').kind == 'referral'
    assert parse_start_payload('ref_123').value == '123'
    assert parse_start_payload('gift_abc').kind == 'gift'
    assert parse_start_payload('gift_abc').value == 'abc'
    assert parse_start_payload('weird').kind == 'plain'
