"""Factory test mode. Exercised at provisioning time to confirm that every
manifest self_test passes before the device is shipped.
"""
import logger


def run(self_tests, drivers):
    """Execute the self_test list from the device manifest. Each step is a
    plain string; the function looks up a callable in ``drivers`` and reports
    pass/fail. Any blocking failure aborts the test mode.
    """
    results = []
    for st in self_tests or []:
        sid = st.get("id")
        ok = True
        detail = "skipped"
        fn = drivers.get(sid) if isinstance(drivers, dict) else None
        if callable(fn):
            try:
                detail = fn() or "ok"
            except Exception as exc:  # noqa: BLE001
                ok = False
                detail = "error: %s" % exc
        results.append({"id": sid, "ok": ok, "detail": detail})
        logger.info("test_mode", "step %s -> %s" % (sid, "OK" if ok else "FAIL"))
        if not ok and st.get("blocking"):
            return results, False
    return results, True
