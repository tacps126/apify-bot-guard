# Security

Please report vulnerabilities privately through GitHub: the repository's Security tab,
"Report a vulnerability". Do not open a public issue.

Things we care about most:

- a way for a free user to get around the limits (other than running many accounts, which the pool
  limits by design);
- raw user ids or tokens reaching the store or the logs;
- a storage error that stops a run instead of failing open.

We aim to answer within a week and to ship a fix in both packages together.
