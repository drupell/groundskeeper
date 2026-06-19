---
name: Bug report
about: Something broke — help us reproduce it.
title: "[bug] "
labels: bug
assignees: ''
---

### What happened
A short, specific description. One paragraph is plenty.

### What you expected
What should have happened instead?

### Steps to reproduce
1.
2.
3.

### Environment
- AWS region:
- Dashboard version (from `frontend/package.json`):
- Browser (if the bug is in the UI):
- Deploy mode (dev / prod):

### CloudWatch log slice
If a Lambda is involved, paste a small, relevant slice. Trim aggressively — 10-30 lines around the error is usually enough. Scrub account IDs and ARNs if you'd rather not share them.

```
(logs here)
```

### Anything else
Screenshots, hunches, related issues, etc.
