Write the section the brief asks for.

Brief:
{{ steps.read_brief.text }}

{% if steps.write %}
Your previous draft:
{{ steps.write.text }}

The review of that draft, as JSON:
{{ steps.review.text }}
{% else %}
This is your first draft: there is no review yet.
{% endif %}
