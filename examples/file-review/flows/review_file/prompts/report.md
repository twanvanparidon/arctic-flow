Write the review for `{{ inputs.path }}`.

Summary:
{{ steps.summarize.text }}

Triage decision: {{ steps.triage.json.verdict }}
Triage reason: {{ steps.triage.json.reason }}

{% if steps.risk_scan %}
Risk findings:
{{ steps.risk_scan.text }}
{% else %}
No risk review was run: triage judged the file clean.
{% endif %}
