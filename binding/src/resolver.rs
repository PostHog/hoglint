//! Name resolution and class ancestry, for packs. Calls share the resolver state the link
//! built, so they answer as the link did.

use crate::link::Link;
use crate::{HitTuple, Pair};
use hoglint_facts::link::facts::{CONSTRUCTORS, COPY_CALLEES, V};
use hoglint_facts::link::intern::Interner;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFloat, PyInt, PyString};

/// Whether call keywords (`[[name, value], ...]`) pass `frozen=True`.
fn frozen_keyword(keywords: &Bound<'_, PyAny>) -> PyResult<bool> {
    for pair in keywords.try_iter()? {
        let pair = pair?;
        if pair.len()? == 2
            && pair.get_item(0)?.eq("frozen")?
            && pair.get_item(1)?.is_truthy()?
            && pair.get_item(1)?.is_instance_of::<PyBool>()
        {
            return Ok(true);
        }
    }
    Ok(false)
}

/// A JSON-like Python value tree (what `orjson` makes of a fact) as the link's V.
fn py_value(it: &mut Interner, obj: &Bound<'_, PyAny>) -> PyResult<V> {
    if obj.is_none() {
        return Ok(V::Null);
    }
    if let Ok(text) = obj.cast::<PyString>() {
        return Ok(V::Chain(it.intern(&text.to_cow()?)));
    }
    if obj.is_instance_of::<PyBool>()
        || obj.is_instance_of::<PyInt>()
        || obj.is_instance_of::<PyFloat>()
    {
        return Ok(V::Scalar);
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
        let frozen = match get("kw")? {
            Some(keywords) => frozen_keyword(&keywords)?,
            None => false,
        };
        let name = callee.extract::<String>().unwrap_or_default();
        let copy = COPY_CALLEES.contains(&name.as_str());
        let constructor = CONSTRUCTORS.contains(&name.as_str()) && get("kw")?.is_none();
        let sole = match get("args")? {
            Some(args) if (copy || constructor) && args.len()? == 1 => {
                Some(Box::new(py_value(it, &args.get_item(0)?)?))
            }
            _ => None,
        };
        return Ok(V::Call(Box::new(py_value(it, &callee)?), frozen, sole));
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
    if get("un")?.is_some() {
        let inner = match get("v")? {
            Some(inner) => py_value(it, &inner)?,
            None => V::Null,
        };
        return Ok(V::Unary(Box::new(inner)));
    }
    if let Some(value) = get("vs")? {
        return Ok(V::BoolOp(items(it, value)?));
    }
    if let Some(first) = get("cmp")? {
        let rest = match get("cs")? {
            Some(value) => items(it, value)?,
            None => Vec::new(),
        };
        return Ok(V::Compare(Box::new(py_value(it, &first)?), rest));
    }
    Ok(V::Other)
}

#[pymethods]
impl Link {
    fn resolve_name(&mut self, module: &str, name: &str) -> PyResult<Option<HitTuple>> {
        self.rows()?;
        let (module, name) = (self.sid(module), self.sid(name));
        let hit = self.inner.core.resolve_name(module, name);
        Ok(self.hit(hit))
    }

    /// (hit or None, attribute names left over).
    fn resolve_dotted(&mut self, dotted: &str) -> PyResult<(Option<HitTuple>, Vec<String>)> {
        self.rows()?;
        let dotted = self.sid(dotted);
        let (hit, rest) = self.inner.core.resolve_dotted(dotted);
        let rest = rest
            .iter()
            .map(|&s| self.inner.core.it.s(s).to_string())
            .collect();
        Ok((self.hit(hit), rest))
    }

    fn resolve_value(
        &mut self,
        module: &str,
        value: &Bound<'_, PyAny>,
    ) -> PyResult<Option<HitTuple>> {
        self.rows()?;
        let module = self.sid(module);
        let value = py_value(&mut self.inner.core.it, value)?;
        let hit = self.inner.core.resolve_value(module, &value);
        Ok(self.hit(hit))
    }

    fn resolve_class(&mut self, module: &str, value: &Bound<'_, PyAny>) -> PyResult<Option<Pair>> {
        self.rows()?;
        let module = self.sid(module);
        let value = py_value(&mut self.inner.core.it, value)?;
        Ok(self
            .inner
            .core
            .resolve_class(module, &value)
            .map(|e| self.pair(e)))
    }

    fn mro(&mut self, module: &str, symbol: &str) -> PyResult<Vec<Pair>> {
        self.rows()?;
        let class = self.ent(module, symbol);
        let mro = self.inner.core.mro(class);
        Ok(mro.into_iter().map(|e| self.pair(e)).collect())
    }

    fn bases(&mut self, module: &str, symbol: &str) -> PyResult<Vec<Pair>> {
        self.rows()?;
        let class = self.ent(module, symbol);
        let bases = self.inner.core.bases(class);
        Ok(bases.into_iter().map(|e| self.pair(e)).collect())
    }

    fn is_subclass(&mut self, module: &str, symbol: &str, qualified: &str) -> PyResult<bool> {
        self.rows()?;
        let class = self.ent(module, symbol);
        let qualified = self.sid(qualified);
        Ok(self.inner.core.is_subclass(class, qualified))
    }

    /// (file id, def index) of the record that defines a class, or None.
    fn located(&mut self, module: &str, symbol: &str) -> PyResult<Option<(u32, u32)>> {
        self.rows()?;
        let class = self.ent(module, symbol);
        Ok(self.inner.core.located(class))
    }

    /// The lookup of a value at a site of the analyzed file with this scope index.
    fn value_lookup(
        &mut self,
        scope: u32,
        value: &Bound<'_, PyAny>,
        at: i32,
        line: u32,
    ) -> PyResult<Option<(String, Option<HitTuple>, bool)>> {
        self.rows()?;
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

    /// The lookup and the attribute names left over.
    #[allow(clippy::type_complexity)]
    fn chain_lookup(
        &mut self,
        scope: u32,
        chain: &str,
        at: i32,
        line: u32,
    ) -> PyResult<((String, Option<HitTuple>, bool), Vec<String>)> {
        self.rows()?;
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
