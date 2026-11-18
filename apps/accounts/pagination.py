"""Páginas acotadas conservando solo los filtros reconocidos por cada vista."""
from django.core.paginator import Paginator


def pagination_context(request, queryset, filter_names=(), per_page=25):
    page = Paginator(queryset, per_page).get_page(request.GET.get('page'))
    query = request.GET.copy()
    for name in list(query):
        if name not in filter_names:
            del query[name]
    encoded = query.urlencode()
    return page, {'page_obj': page, 'pagination_query': encoded + '&' if encoded else ''}
