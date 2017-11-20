eb create newsdiffs-worker-prod \
  --tier worker \
  --elb-type network \
  --keyname newsdiffs-key-pair \
  --vpc.securitygroups newsdiffs-prod \
  --cfg worker-prod
