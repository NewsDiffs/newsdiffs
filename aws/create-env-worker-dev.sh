eb create newsdiffs-worker-dev \
  --single \
  --tier worker \
  --vpc.securitygroups newsdiffs-dev \
  --cfg worker-dev
