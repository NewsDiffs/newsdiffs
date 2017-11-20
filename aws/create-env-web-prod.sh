eb create newsdiffs-web \
  --cname newsdiffs \
  --elb-type network \
  --keyname newsdiffs-key-pair \
  --vpc.securitygroups newsdiffs-prod \
  --cfg web-prod
