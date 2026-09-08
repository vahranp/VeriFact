"""VeriFact evaluation package.

This package is intentionally never imported by app/ (the production
inference pipeline). It observes production's outputs and measures them
against independently-assigned ground truth; it must never feed anything
back into how a document is actually processed. See evaluation/README.md
for the full methodology.
"""
