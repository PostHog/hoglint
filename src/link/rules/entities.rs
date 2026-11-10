//! Rules over entities (hoglint/rules/entities.py): a layout rule's `select` and `require` on
//! every module or symbol, an inventory's `exactly_one`, the roots a layout rule's `within`
//! names, and the modules a registry's glob holds.

use super::Rules;
use super::env::{Cx, EntityEnv};
use super::glob::{Glob, fill};
use super::plan::EntitiesPlan;
use super::selector::{Compiler, Ref, Resolve, evaluate};
use super::staged::class_truth;
use super::value::{Truth, Value, render};
use crate::link::Link;
use crate::link::intern::Sid;

/// What a rule over entities adds to a rule.
pub struct EntityRule {
    on_symbol: bool,
    /// The kinds a `select` pins, so on symbols only their members and the symbols of unknown
    /// kind are read (`EntityRule.entities`).
    wanted: Option<Vec<String>>,
    /// An inventory's `exactly_one` field.
    exactly_one: Option<Ref>,
    identity: Vec<Ref>,
}

impl EntityRule {
    pub fn new(plan: &EntitiesPlan, identity: &[Vec<String>], entity: &Compiler) -> EntityRule {
        EntityRule {
            on_symbol: plan.on_symbol,
            wanted: plan.wanted.clone(),
            exactly_one: plan
                .exactly_one
                .as_ref()
                .map(|name| entity.reference(std::slice::from_ref(name))),
            identity: identity.iter().map(|path| entity.reference(path)).collect(),
        }
    }

    pub fn refs(&self, out: &mut Vec<Ref>) {
        out.extend(&self.identity);
        out.extend(self.exactly_one);
    }
}

/// (module, its place, symbol, subject) of one entity a rule looks at.
type Entity = (Option<Sid>, Option<u32>, Option<String>, String);

/// What a rule over entities reports, in order, as data the rule's Python class words
/// (`EntityRule.report_rows`): (what, subject, module, count, identity). What: "finding",
/// "unknown-selection" (whether the entity is selected), or "unknown-value" (an inventory's
/// field); the count of an inventory's values, else 0; the identity of a finding, else empty.
pub type EntityRow = (&'static str, String, Option<String>, u32, Vec<String>);

impl Rules {
    /// `EntityRule.entities`: (module, its place, symbol, subject) of every entity the rule
    /// looks at, in the Python rule's order.
    fn entities(&self, link: &Link, rule: &EntityRule) -> Vec<Entity> {
        let cx = Cx { link, rules: self };
        let it = &link.core.it;
        if !rule.on_symbol {
            let mut modules: Vec<(&str, Sid, u32)> = self
                .modules
                .iter()
                .enumerate()
                .map(|(at, module)| (it.s(*module), *module, at as u32))
                .collect();
            modules.sort();
            return modules
                .into_iter()
                .map(|(name, module, at)| (Some(module), Some(at), None, name.to_string()))
                .collect();
        }
        let mut symbols: Vec<String> = match &rule.wanted {
            None => super::symbols::SymbolFacts::all_symbols(link)
                .into_iter()
                .map(|sid| it.s(sid).to_string())
                .collect(),
            Some(wanted) => self.kind_candidates(link, wanted),
        };
        symbols.sort();
        symbols.dedup();
        symbols
            .into_iter()
            .map(|symbol| {
                let owner = cx.module_of_symbol(&symbol);
                (
                    owner.map(|(module, _)| module),
                    owner.map(|(_, at)| at),
                    Some(symbol.clone()),
                    symbol,
                )
            })
            .collect()
    }

    /// `Labeler.kind_candidates`: the symbols with facts whose kind is not known to be outside
    /// `wanted`.
    fn kind_candidates(&self, link: &Link, wanted: &[String]) -> Vec<String> {
        let wanted: Vec<usize> = self
            .kind_names
            .iter()
            .enumerate()
            .filter(|(_, name)| wanted.contains(name))
            .map(|(at, _)| at)
            .collect();
        let sets = &self.kind_sets;
        let mut out: Vec<String> = sets.unknown.iter().cloned().collect();
        out.extend(
            sets.kinds_of
                .iter()
                .filter(|(_, kinds)| kinds.len() > 1 || kinds.iter().any(|at| wanted.contains(at)))
                .map(|(name, _)| name.clone()),
        );
        out.retain(|name| self.has_symbol(link, name));
        out
    }

    /// What layout rule or entity inventory `index` reports on its entities
    /// (`LayoutPrimitive.check_entities`, `EntityInventory.evaluate`).
    pub fn entity_rows(&self, link: &Link, index: usize) -> Result<Vec<EntityRow>, String> {
        let rule = self.rule(index, super::plan::Mode::Entities)?;
        let Some(entities) = &rule.entities else {
            return Err(format!("rule {} has no entity plan", rule.id));
        };
        let cx = Cx { link, rules: self };
        let mut out = Vec::new();
        for (module, at, symbol, subject) in self.entities(link, entities) {
            let env = EntityEnv {
                cx,
                module,
                at,
                symbol: symbol.as_deref(),
                subject: Some(&subject),
            };
            let module_name = module.map(|module| cx.text(module).to_string());
            let selected = class_truth(&rule.selection, &env, self.touch);
            if selected == Truth::Unknown {
                let (unselected, module) = (subject.clone(), module_name.clone());
                out.push(("unknown-selection", unselected, module, 0, Vec::new()));
            }
            if selected != Truth::True {
                continue;
            }
            let count = match entities.exactly_one {
                Some(field) => {
                    let count = match env.resolve(field) {
                        Value::Unknown => {
                            out.push(("unknown-value", subject, module_name, 0, Vec::new()));
                            continue;
                        }
                        Value::Set(items) => items.len() as u32,
                        _ => 0,
                    };
                    if count == 1 {
                        continue;
                    }
                    count
                }
                None => {
                    let verdict = match &rule.require {
                        Some(node) => evaluate(node, &env),
                        None => Truth::False,
                    };
                    if verdict == Truth::True {
                        continue;
                    }
                    0
                }
            };
            let identity = entities
                .identity
                .iter()
                .map(|field| render(&env.resolve(*field)))
                .collect();
            out.push(("finding", subject, module_name, count, identity));
        }
        Ok(out)
    }

    /// `LevelEvaluator.groups`: the text of field `group_by` on every graph module where it is
    /// known and the level's `select` (a plan tree as JSON, or none) is true, sorted.
    pub fn level_groups(
        &self,
        link: &Link,
        group_by: &str,
        select: Option<&str>,
    ) -> Result<Vec<String>, String> {
        let names: Vec<String> = self
            .labels
            .fields
            .iter()
            .map(|field| field.name.clone())
            .collect();
        let entity = Compiler {
            fields: &names,
            it: &link.core.it,
            reading: super::selector::Reading::Entity,
        };
        let node = match select {
            None => None,
            Some(text) => {
                let tree: super::plan::NodePlan = serde_json::from_str(text)
                    .map_err(|error| format!("a selector that does not parse: {error}"))?;
                Some(entity.node(&tree)?)
            }
        };
        let field = entity.reference(&[group_by.to_string()]);
        let mut refs = vec![field];
        if let Some(node) = &node {
            node.refs(&mut refs);
        }
        super::check_columns(&self.labels, "a level", &refs)?;
        let cx = Cx { link, rules: self };
        let mut out: Vec<String> = Vec::new();
        for (at, module) in self.modules.iter().enumerate() {
            let env = EntityEnv {
                cx,
                module: Some(*module),
                at: Some(at as u32),
                symbol: None,
                subject: None,
            };
            let group = match env.resolve(field) {
                Value::Text(text) => text.into_owned(),
                Value::Zone(zone) => zone.name.clone(),
                Value::Bool(flag) => if flag { "True" } else { "False" }.to_string(),
                Value::Unknown | Value::None => continue,
                other => return Err(format!("a level groups by a field read as {other:?}")),
            };
            if let Some(node) = &node
                && evaluate(node, &env) != Truth::True
            {
                continue;
            }
            out.push(group);
        }
        out.sort();
        out.dedup();
        Ok(out)
    }

    /// `LayoutPrimitive.roots`: the roots `within` names among the graph's module paths, sorted.
    pub fn layout_roots(&self, link: &Link, within: &str) -> Result<Vec<String>, String> {
        let glob = Glob::new(&format!("{within}/**"))?;
        let mut out: Vec<String> = (0..self.modules.len() as u32)
            .filter_map(|at| {
                glob.captures(self.module_path(link, at))
                    .map(|captures| fill(within, &captures))
            })
            .collect();
        out.sort();
        out.dedup();
        Ok(out)
    }

    /// The graph's (module, path) whose path a registry's `module` glob matches, by module name
    /// (`LayoutPrimitive.check_registry`).
    pub fn registry_modules(
        &self,
        link: &Link,
        pattern: &str,
    ) -> Result<Vec<(String, String)>, String> {
        let glob = Glob::new(pattern)?;
        let it = &link.core.it;
        let mut out: Vec<(String, String)> = (0..self.modules.len() as u32)
            .filter(|at| glob.is_match(self.module_path(link, *at)))
            .map(|at| {
                (
                    it.s(self.modules[at as usize]).to_string(),
                    self.module_path(link, at).to_string(),
                )
            })
            .collect();
        out.sort();
        Ok(out)
    }
}
