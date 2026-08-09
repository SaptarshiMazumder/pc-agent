"""Publish-service adapters — the AWS half of ``application/interfaces/publish_intake.py``.

Nothing in the intake service imports anything from here; the composition root in
``v2/services/publish/handler.py`` wires them. That is what lets the whole publish path be tested
with fakes and no AWS account.
"""
