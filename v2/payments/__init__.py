"""The payment rail, as its own module with its own layers.

WHY IT IS NOT PART OF `accounts`. Accounts owns the BOOKS — who has credits, what was spent,
what a creator is owed. Payments owns the RAIL — asking a third party to move money and being
told, later and out of band, whether it did. Those fail differently, are tested differently, and
change for different reasons: a rail swap must not touch the ledger, and a pricing change must
not touch Stripe.

THE DEPENDENCY RUNS BOTH WAYS, THROUGH INTERFACES ONLY:

    accounts  ──uses──▶  PaymentGateway         (declared here, implemented here)
    payments  ──uses──▶  PaymentsPostProcessor  (declared here, implemented by accounts)

So this module never learns what a credit grant is, and accounts never imports `stripe`.

WHAT IT IS NOT. Not a service. A purchase and its ledger posting must commit together, and
splitting them across a network hop turns one transaction into a distributed one — for a system
whose entire job is being exactly right about money. It is a package, imported in-process by the
accounts service, exactly like `agentd_telemetry`.
"""
