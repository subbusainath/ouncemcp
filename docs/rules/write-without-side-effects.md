# `write-without-side-effects`

Severity: Warning. Reported by `ouncemcp check`.

## What it finds

A write or destructive tool's description does not mention what it changes.

## Why it matters

Models and the people approving calls need to know what a call will change before making it (spec CAP-5).

## How to fix it

Add a sentence such as "Side effect: notifies the requester." or "Deletes the ticket permanently."
