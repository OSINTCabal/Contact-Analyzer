## Summary

Describe the behavior changed and why.

## Validation

- [ ] `bash -n install.sh contactanalyzer scripts/launch-chrome.sh`
- [ ] `python -m unittest discover -s tests -v`
- [ ] New or changed normalizers include accepted and rejected URL tests
- [ ] No browser profiles, credentials, case bundles, databases, or real subject data
- [ ] Incomplete collections remain explicitly incomplete
