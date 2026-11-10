#!/usr/bin/env bash
# T-1 mutation checks: break each guard, run the tests, show which named tests fail, restore.
# Usage: mutate.sh <repo root>   (writes to stdout)
set -uo pipefail
cd "$1"
set -a; . ./.env; set +a
SRC=libs/py-data/src/onb_data
PY="uv run --frozen"

unit() { $PY pytest libs/py-data/tests -q -p no:cacheprovider -o addopts="--import-mode=importlib" 2>&1 | grep -E '^FAILED|^ERROR| passed| failed' ; }
integ() { $PY pytest libs/py-data/tests_int -q -p no:cacheprovider -o addopts="--import-mode=importlib" 2>&1 | grep -E '^FAILED|^ERROR| passed| failed' ; }

mutate() {  # id, file, perl expression, description, run-integration(yes/no)
  local id=$1 file=$2 expr=$3 desc=$4 int=$5
  echo "=================================================================="
  echo "$id: $desc"
  echo "file: $file"
  perl -0pi -e "$expr" "$file"
  if git diff --quiet -- "$file"; then echo "!! mutation did not apply"; return; fi
  git diff --unified=0 -- "$file" | grep -E '^[-+][^-+]'
  echo "--- unit tests (libs/py-data/tests)"
  unit
  if [ "$int" = yes ]; then echo "--- integration tests (libs/py-data/tests_int, compose Mongo)"; integ; fi
  git checkout -- "$file"
  git diff --quiet -- "$file" && echo "restored: $file (0 files differ)"
}

mutate M1 $SRC/scoping.py 's/return \[\{"\$match": \{ORG_FIELD: org_id\}\}, \*stages\]/return stages/' \
  'box 3: remove the $match prepend from scoped_pipeline' yes
mutate M2 $SRC/scoping.py 's/return \[\{"\$match": \{ORG_FIELD: org_id\}\}, \*stages\]/return [*stages, {"\$match": {ORG_FIELD: org_id}}]/' \
  'trap: $match appended last instead of first (after a leading $sort)' no
mutate M3 $SRC/scoping.py 's/    return \{\*\*base, ORG_FIELD: org_id\}/    return base/' \
  'filters no longer get org_id' yes
mutate M4 $SRC/context.py 's/    if ctx is None:\n        raise NoTenantContextError\(.*?\)\n/    if ctx is None:\n        return TenantContext("org_default")\n/' \
  'no context falls back to a default org instead of raising' yes
mutate M5 $SRC/errors.py 's/class NotFoundError\(DataAccessError\):/class NotFoundError(DataAccessError, PermissionError):/' \
  'NotFound becomes a permission error' yes
mutate M6 $SRC/repository.py 's/        if found is None:\n            raise self._not_found\(\)\n        return found/        return found  # type: ignore[return-value]/' \
  'get returns None for a foreign id instead of raising NotFound' yes
mutate M7 $SRC/scoping.py 's/    if not upsert:\n        return checked\n/    return checked\n/' \
  'trap: an upsert no longer sets org_id via $setOnInsert' yes
mutate M8 $SRC/scoping.py 's/if tenant and any\(_touches_org/if False and any(_touches_org/' \
  'updates may $set / $unset / $rename org_id' no
mutate M9 $SRC/scoping.py 's/    if ORG_FIELD in base and base\[ORG_FIELD\] != org_id:\n        raise TenantViolationError\("a document may not carry another org_id"\)\n    return \{\*\*base, ORG_FIELD: org_id\}/    return base/' \
  'inserts no longer set org_id (and accept another org)' yes
mutate M10 $SRC/scoping.py 's/    if ORG_FIELD in base and base\[ORG_FIELD\] != org_id:\n        raise TenantViolationError\(.*?\)\n//' \
  'a filter may name another org_id' no
mutate M11 $SRC/scoping.py 's/    forbidden = _find_cross_collection_stage\(stages\)\n/    forbidden = None\n/' \
  'pipelines may use $lookup / $unionWith / $out / $merge' no
mutate M12 $SRC/scoping.py 's/    if tenant and fields\[0\] != ORG_FIELD:/    if False:/' \
  'tenant indexes need not start with org_id' no
mutate M13 $SRC/store.py 's/        if name in GLOBAL_COLLECTIONS:\n            raise UnknownCollectionError/        if False:\n            raise UnknownCollectionError/' \
  'a global collection is handed out as a tenant one' no
mutate M14 $SRC/repository.py 's/if TYPE_CHECKING:\n    from pymongo.asynchronous.collection import AsyncCollection\n/from pymongo.asynchronous.collection import AsyncCollection\n/' \
  'box 4: repository.py imports AsyncCollection at runtime (a public module attribute)' no
mutate M15 $SRC/store.py 's/    __slots__ = \("_client", "_database"\)\n/    __slots__ = ("_client", "_database", "database")\n/; s/        self._database = database\n/        self._database = database\n        self.database = database\n/' \
  'box 4: DataStore exposes its database publicly' no

mutate M16 $SRC/scoping.py 's/    if isinstance\(node, list \| tuple\):\n        return \[_plain/    if isinstance(node, list):\n        return [_plain/' \
  'review fix: tuples are not normalised to lists (a tuple $facet sub-pipeline goes unchecked)' no
mutate M17 $SRC/repository.py 's/        if isinstance\(doc_id, Mapping \| list \| tuple \| set \| frozenset\):\n            raise TypeError\(.*?\)\n//' \
  'review fix: an operator such as {"$ne": None} is accepted as an id' no
mutate M18 $SRC/scoping.py 's/            if type\(key\) is not str:\n                raise TypeError\(.*?\)\n//' \
  'review fix: str-subclass keys that hide from == are accepted' no
mutate M19 $SRC/scoping.py 's/    if first_only:\n        raise TenantViolationError\(.*?\)\n//' \
  'review fix: first-stage-only operators reach the server after the org $match' no

echo "=================================================================="
git status --short libs/py-data
echo "worktree diff after all mutations: $(git diff --name-only -- libs/py-data | wc -l | tr -d ' ') files differ"
