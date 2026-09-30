"""Sortable table headers: `{% load sorting %}`. The context needs ``sort`` and ``request``."""

from django import template
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from club.sorting import MOBILE_LABELS, sort_url

register = template.Library()


def _link(context, key: str, label, sr_label: str = "", css: str = "sort-link"):
    sort = context["sort"]
    active = sort.key == key
    if active:
        css += " sort-desc" if sort.descending else " sort-asc"
    text = (
        format_html(
            '<span aria-hidden="true">{}</span><span class="sr-only">{}</span>', label, sr_label
        )
        if sr_label
        else label
    )
    return format_html(
        '<a class="{}" href="{}"{}>{}</a>',
        css,
        sort_url(context["request"].path, context["request"].GET, sort.toggled(key)),
        mark_safe(' aria-current="true"') if active else "",
        text,
    )


@register.simple_tag(takes_context=True)
def sort_header(context, key: str, label: str, sr_label: str = ""):
    """A column header link: sorts by ``key``, or flips the direction if it is the active one."""
    return _link(context, key, label, sr_label)


@register.simple_tag(takes_context=True)
def sort_header_lines(context, key: str, lines, label: str):
    """A header link whose text is broken onto ``lines`` (a narrow column under a long name, or
    one string for a one-line header of the same row); screen readers get ``label`` in one
    piece. The text and the arrow sit at the bottom, so every header of the row ends on the same
    line."""
    if isinstance(lines, str):
        lines = (lines,)
    text = format_html(
        '<span class="sort-lines">{}</span>',
        format_html_join(mark_safe("<br>"), "{}", ((line,) for line in lines)),
    )
    return _link(context, key, text, label, css="sort-link sort-link-lines")


@register.simple_tag(takes_context=True)
def aria_sort(context, key: str):
    """``aria-sort`` for the header cell of the active column."""
    sort = context["sort"]
    if sort.key != key:
        return ""
    return format_html(' aria-sort="{}"', "descending" if sort.descending else "ascending")


@register.simple_tag(takes_context=True)
def mobile_sort_links(context):
    """ "итог · игры · ..." for narrow screens, where most columns are hidden."""
    links = [_link(context, key, MOBILE_LABELS[key]) for key in context["sort"].table.mobile]
    separator = mark_safe('<span aria-hidden="true"> · </span>')
    return mark_safe(separator.join(links))  # the links are built by format_html
