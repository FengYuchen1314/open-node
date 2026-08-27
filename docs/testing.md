# Testing

All authoritative tests for Open Node run on the VPS at `185.99.135.224` over
SSH. Local runs are useful during editing, but they are not the final gate.

## Remote Test Command

From Windows PowerShell in the repository root:

```powershell
.\scripts\vps\sync-and-test.ps1
```

The script uses the default SSH key for `root@185.99.135.224`, checks out the
GitHub repository into `/opt/open-node`, and runs:

1. backend dependency installation;
2. backend pytest suite;
3. frontend dependency installation;
4. frontend Vitest suite;
5. frontend production build.

## Direct VPS Command

If the repository is already checked out on the VPS:

```bash
cd /opt/open-node
bash scripts/vps/run-tests.sh
```
