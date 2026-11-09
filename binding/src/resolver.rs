//! Name resolution and class ancestry, for packs. Calls share the resolver state the link
//! built, so they answer as the link did.

use crate::link::Link;
use crate::{HitTuple, Pair};
use hoglint_facts::link::facts::V;
use hoglint_facts::link::intern::Interner;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyString};

/// A JSON-like Python value tree (what `orjson` makes of a fact) as the link's V.
fn py_value(it: &mut Interner, obj: &Bound<'_, PyAny>) -> PyResult<V> {
    if obj.is_none() {
        return Ok(V::Null);
    }
    if let Ok(text) = obj.cast::<PyString>() {
        return Ok(V::Chain(it.intern(&text.to_cow()?)));
    }
    let Ok(map) = obj.cast::<PyDict>() else {
        return Ok(V::Other);
    };
    let get = |key: &str| map.get_item(key);
    let items = |it: &mut Interner, value: Bound<'_, PyAny>| -> PyResult<Vec<V>> {
        let mut out = Vec::new();
        for item in value.try_iter()? {
            out.push(py_value(it, &item?)?);
        }
        Ok(out)
    };
    if let Some(inner) = get("sub")? {
        let index = match get("i")? {
            Some(index) => py_value(it, &index)?,
            None => V::Null,
        };
        return Ok(V::Sub(Box::new(py_value(it, &inner)?), Box::new(index)));
    }
    if let Some(s) = get("s")? {
        let parsed = match get("sa")? {
            Some(sa) => Some(Box::new(py_value(it, &sa)?)),
            None => None,
        };
        let text: String = s.extract().unwrap_or_default();
        return Ok(V::Str(it.intern(&text), parsed));
    }
    if let Some(callee) = get("call")? {
        return Ok(V::Call(Box::new(py_value(it, &callee)?)));
    }
    if let (Some(a), Some(v)) = (get("a")?, get("v")?) {
        let attr: String = a.extract().unwrap_or_default();
        let attr = it.intern(&attr);
        return Ok(V::Attr(Box::new(py_value(it, &v)?), attr));
    }
    if let Some(value) = get("tuple")? {
        return Ok(V::Tuple(items(it, value)?));
    }
    if let Some(value) = get("list")? {
        return Ok(V::List(items(it, value)?));
    }
    if let Some(value) = get("set")? {
        return Ok(V::Set(items(it, value)?));
    }
    if let Some(value) = get("dict")? {
        let mut pairs = Vec::new();
        for pair in value.try_iter()? {
            let pair = pair?;
            let key = pair.get_item(0)?;
            let key = if key.is_none() {
                None
            } else {
                Some(py_value(it, &key)?)
            };
            pairs.push((key, py_value(it, &pair.get_item(1)?)?));
        }
        return Ok(V::Dict(pairs));
    }
    if let Some(inner) = get("star")? {
        return Ok(V::Star(Box::new(py_value(it, &inner)?)));
    }
    if let Some(op) = get("bin")? {
        let pipe = op.extract::<String>().is_ok_and(|op| op == "|");
        let l = match get("l")? {
            Some(l) => py_value(it, &l)?,
            None => V::Null,
        };
        let r = match get("r")? {
            Some(r) => py_value(it, &r)?,
            None => V::Null,
        };
        return Ok(V::Bin(pipe, Box::new(l), Box::new(r)));
    }
    if let Some(what) = get("o")? {
        let what: String = what.extract().unwrap_or_default();
        return Ok(V::Opaque(it.intern(&what)));
    }
    Ok(V::Other)
}

#[pymethods]
impl Link {
    fn resolve_name(&mut self, module: &str, name: &str) -> Option<HitTuple> {
        let (module, name) = (self.sid(module), self.sid(name));
        let hit = self.inner.core.resolve_name(module, name);
        self.hit(hit)
    }

    /// (hit or None, attribute names left over).
    fn resolve_dotted(&mut self, dotted: &str) -> (Option<HitTuple>, Vec<String>) {
        let dotted = self.sid(dotted);
        let (hit, rest) = self.inner.core.resolve_dotted(dotted);
        let rest = rest
            .iter()
            .map(|&s| self.inner.core.it.s(s).to_string())
            .collect();
        (self.hit(hit), rest)
    }

    fn resolve_value(
        &mut self,
        module: &str,
        value: &Bound<'_, PyAny>,
    ) -> PyResult<Option<HitTuple>> {
        let module = self.sid(module);
        let value = py_value(&mut self.inner.core.it, value)?;
        let hit = self.inner.core.resolve_value(module, &value);
        Ok(self.hit(hit))
    }

    fn resolve_class(&mut self, module: &str, value: &Bound<'_, PyAny>) -> PyResult<Option<Pair>> {
        let module = self.sid(module);
        let value = py_value(&mut self.inner.core.it, value)?;
        Ok(self
            .inner
            .core
            .resolve_class(module, &value)
            .map(|e| self.pair(e)))
    }

    fn mro(&mut self, module: &str, symbol: &str) -> Vec<Pair> {
        let class = self.ent(module, symbol);
        let mro = self.inner.core.mro(class);
        mro.into_iter().map(|e| self.pair(e)).collect()
    }

    fn bases(&mut self, module: &str, symbol: &str) -> Vec<Pair> {
        let class = self.ent(module, symbol);
        let bases = self.inner.core.bases(class);
        bases.into_iter().map(|e| self.pair(e)).collect()
    }

    fn is_subclass(&mut self, module: &str, symbol: &str, qualified: &str) -> bool {
        let class = self.ent(module, symbol);
        let qualified = self.sid(qualified);
        self.inner.core.is_subclass(class, qualified)
    }

    /// (file id, def index) of the record that defines a class, or None.
    fn located(&mut self, module: &str, symbol: &str) -> Option<(u32, u32)> {
        let class = self.ent(module, symbol);
        self.inner.core.located(class)
    }

    /// `BuildContext.value_lookup` at a site of the analyzed file with this scope index.
    fn value_lookup(
        &mut self,
        scope: u32,
        value: &Bound<'_, PyAny>,
        at: i32,
        line: u32,
    ) -> PyResult<Option<(String, Option<HitTuple>, bool)>> {
        let index = self.scope_index(scope)?;
        let value = py_value(&mut self.inner.core.it, value)?;
        let link = &mut self.inner;
        let found = link.core.value_lookup(
            &link.settings.names,
            &mut link.scopes[index],
            &value,
            at,
            line,
        );
        Ok(found.map(|found| self.lookup(found)))
    }

    /// `BuildContext.chain_lookup`: the lookup and the attribute names left over.
    #[allow(clippy::type_complexity)]
    fn chain_lookup(
        &mut self,
        scope: u32,
        chain: &str,
        at: i32,
        line: u32,
    ) -> PyResult<((String, Option<HitTuple>, bool), Vec<String>)> {
        let index = self.scope_index(scope)?;
        let chain = self.sid(chain);
        let link = &mut self.inner;
        let (found, rest) = link.core.chain_lookup(
            &link.settings.names,
            &mut link.scopes[index],
            chain,
            at,
            line,
        );
        let rest = rest
            .iter()
            .map(|&s| self.inner.core.it.s(s).to_string())
            .collect();
        Ok((self.lookup(found), rest))
    }
}
