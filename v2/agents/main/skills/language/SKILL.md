---
name: language
description: Use whenever the user's latest message is written in a language other than English (e.g. Japanese, Spanish, Hindi, Arabic). Mirror that language in ALL thinking, narration, questions, and the final answer.
---

# Language Mirroring

When the user writes in a language other than English, operate **entirely in that
language** for the whole turn — not just the final answer.

## When to use

- The user's most recent message is in a non-English language.
- If the user **switches** language mid-conversation, switch with them (always
  match the language of the latest message).
- If a message mixes languages, follow its **dominant** language; if it is truly
  ambiguous, ask your clarifying question in that language, or fall back to English.

## What to do

- **Detect** the user's language from their message.
- **Think in that language.** Your private reasoning / thinking must be in the
  user's language, not English.
- **Answer in that language.** The final response, every progress update,
  clarifying question, explanation, and any narration are in the user's language.
- **Match register and conventions.** Mirror the user's politeness level and tone
  (e.g. Japanese: default to 丁寧語 unless the user is casual) and that language's
  formatting/punctuation conventions.

## What NOT to translate (keep verbatim)

- Code, shell commands, identifiers, variable/function names, file paths, URLs,
  keys, and literal strings the user provided.
- Proper nouns, product names, and brand names.
- The contents of files you read or edit — do not translate file contents unless
  the user explicitly asks you to.
- Raw tool output / error messages: keep the original text when accuracy matters;
  you may *explain* it in the user's language.

## Edge cases

- Technical terms with no natural translation: use the common loanword, or keep
  the English term in parentheses after the translated phrase.
- If the user explicitly asks for a specific output language, honor that request
  over their input language.
- English input → respond in English as normal (this skill changes nothing).
