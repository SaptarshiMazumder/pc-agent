# Production DNS bootstrap: adopt the existing zone without creating another.
import {
  to = module.stack.aws_route53_zone.main[0]
  id = "Z03906613MU46UB1NOGY1"
}
