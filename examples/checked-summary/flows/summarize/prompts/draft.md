Summarise this file in at most 60 words.

Path: {{ inputs.path }}

---
{{ steps.read_target.text }}
---
{% if steps.check %}
Your last summary was rejected by word_limit: {{ steps.check.json.reason }}

It said:

{{ steps.draft.text }}

Write it again, inside the limit.
{% endif %}
