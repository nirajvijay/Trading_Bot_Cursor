# AWS and Lightsail workflow

## Verified local access

- AWS CLI authentication uses the local `aws login` session.
- Default region for this project is `ap-south-1`.
- Lightsail instance: `nifty-radar-observation-01`.
- Server shell route: `ssh nifty-radar-aws`.

## Before AWS work

Run read-only checks first:

```bash
aws sts get-caller-identity --query '{Account:Account,Arn:Arn}' --output json
aws lightsail get-instances --region ap-south-1
```

If credentials are expired, ask NJ before running `aws login`.

## Safety

- Prefer AWS CLI and the configured SSH alias over the broken remote `aws-mcp` connector.
- Do not print credentials, private keys, tokens, or secret values.
- Treat inspection as read-only by default.
- Ask for explicit approval immediately before deployments, restarts, process kills, file overwrites, firewall/port changes, live trading, or other mutating AWS/server actions.
- Preserve PAPER/LIVE trading safety and verify the target instance before any mutation.
