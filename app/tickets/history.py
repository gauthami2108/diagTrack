"""Series of policies and licences: every term of the same cover (or the same licence) in date order.

Two records belong to one series when one was made by renewing the other (renewed_from), or when they have the same cover
type and the same centres (policies) or the same name (licences). Archived and renewed records stay in the series, so the
whole story of a cover is in one place. Gaps and overlaps between neighbouring terms are flagged."""
from collections import defaultdict


def _find(parent, a):
    while parent[a] != a:
        parent[a] = parent[parent[a]]
        a = parent[a]
    return a


def build_series(items, key, start, end, parent_id):
    """items: model instances. key(x) groups automatically; start/end give the dates; parent_id(x) the renewed_from id.
    Returns lists of terms (oldest first); each term gets .gap (text or "") compared with the live term before it."""
    by_id = {x.pk: x for x in items}
    parent = {x.pk: x.pk for x in items}
    first_of_key = {}
    for x in items:
        k = key(x)
        if k in first_of_key:
            parent[_find(parent, x.pk)] = _find(parent, first_of_key[k])
        else:
            first_of_key[k] = x.pk
        pid = parent_id(x)
        if pid in by_id:
            parent[_find(parent, x.pk)] = _find(parent, pid)
    groups = defaultdict(list)
    for x in items:
        groups[_find(parent, x.pk)].append(x)
    out = []
    for terms in groups.values():
        terms.sort(key=lambda x: (start(x), end(x), x.pk))
        prev = None
        for t in terms:
            t.gap = ""
            if t.archived_at:
                continue
            if prev is not None:
                days = (start(t) - end(prev)).days - 1
                if days >= 1:
                    t.gap = f"No cover or valid licence for {days} day{'s' if days != 1 else ''} before this term"
                elif start(t) <= end(prev):
                    over = (end(prev) - start(t)).days + 1
                    t.gap = f"Overlaps the term before by {over} day{'s' if over != 1 else ''}"
            prev = t
        out.append(terms)
    return out


def policy_series(policies):
    def key(p):
        return (p.cover_type_id, frozenset(c.pk for c in p.centres.all()))
    return build_series(list(policies), key, lambda p: p.start_date, lambda p: p.expiry_date, lambda p: p.renewed_from_id)


def licence_series(licences):
    return build_series(list(licences), lambda x: x.name.strip().lower(), lambda x: x.issued_on, lambda x: x.valid_until,
                        lambda x: x.renewed_from_id)


def chain_ids(item):
    """The ids of every record linked to this one by renewal, in either direction."""
    seen, todo = {item.pk}, [item]
    while todo:
        x = todo.pop()
        for y in ([x.renewed_from] if x.renewed_from_id else []) + list(x.renewals.all()):
            if y.pk not in seen:
                seen.add(y.pk)
                todo.append(y)
    return seen


def descendants(item):
    seen, todo = set(), [item]
    while todo:
        for y in todo.pop().renewals.all():
            if y.pk not in seen:
                seen.add(y.pk)
                todo.append(y)
    return seen


def series_for(item, qs, series_fn, same):
    """The series (oldest first) that holds item. qs is what the person may see; same is the automatic match
    (for example {"cover_type_id": 3}); renewal links are followed whatever the match."""
    from django.db.models import Q
    cand = qs.filter(Q(**same) | Q(pk__in=chain_ids(item))).distinct()
    for terms in series_fn(cand):
        if any(t.pk == item.pk for t in terms):
            return terms
    return [item]


def relink(item, new, actor, model, record):
    """Point item at the earlier record it continues (or at none). Keeps the renewed flags right and logs both sides.
    record(obj, actor, text) writes a history line."""
    old = item.renewed_from
    if (old.pk if old else None) == (new.pk if new else None):
        return False
    item.renewed_from = new
    item.save(update_fields=["renewed_from"])
    if old and not old.renewals.filter(archived_at__isnull=True).exists():
        model.objects.filter(pk=old.pk).update(renewed=False)
        record(old, actor, "Current again: it is no longer linked as renewed by a later term")
    if new:
        model.objects.filter(pk=new.pk).update(renewed=True)
        record(new, actor, "Linked as renewed by a later term")
        record(item, actor, "Linked to the earlier term it continues")
    else:
        record(item, actor, "Link to the earlier term removed")
    return True
