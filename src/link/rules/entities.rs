//! Rules over entities (hoglint/rules/entities.py): a layout rule's `select` and `require` on
//! every module or symbol, an inventory's `exactly_one`, the roots a layout rule's `within`
//! names, and the modules a registry's glob holds.

use super::env::{Cx, EntityEnv};
use super::glob::{Glob, fill};
use super::plan::EntitiesRulePlan;
use super::selector::{Compiler, Node, Ref, Resolve, evaluate};
use super::staged::class_truth;
use super::value::{Truth, Value, render};
use super::{RuleKind, Rules};
use crate::link::Link;
use crate::link::intern::Sid;

/// A rule over entities: a layout rule or an entity inventory.
pub struct EntityRule {
    /// Its `select`, in its `and` parts, and `require`: entity selectors.
    selection: Vec<Node>,
    require: Option<Node>,
    on_symbol: bool,
    /// The kinds a `select` pins, so on symbols only their members and the symbols of unknown
    /// kind are read.
    wanted: Option<Vec<String>>,
    /// An inventory's `exactly_one` field.
    exactly_one: Option<Ref>,
    identity: Vec<Ref>,
}

impl EntityRule {
    pub fn new(plan: &EntitiesRulePlan, entity: &Compiler) -> Result<EntityRule, String> {
        let entities = &plan.entities;
        Ok(EntityRule {
            selection: entity.nodes(&plan.selection)?,
            require: plan
                .require
                .as_ref()
                .map(|node| entity.node(node))
                .transpose()?,
            on_symbol: entities.on_symbol,
            wanted: entities.wanted.clone(),
            exactly_one: entities
                .exactly_one
                .as_ref()
                .map(|name| entity.reference(std::slice::from_ref(name))),
            identity: plan
                .identity
                .iter()
                .map(|path| entity.reference(path))
                .collect(),
        })
    }

    pub fn nodes(&self) -> Vec<&Node> {
        self.selection.iter().chain(&self.require).collect()
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
    /// (module, its place, symbol, subject) of every entity the rule looks at: the graph's
    /// modules or the symbols, sorted by name.
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

    /// The symbols with facts whose kind is not known to be outside `wanted`.
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

    /// What layout rule or entity inventory `index` reports on its entities.
    pub fn entity_rows(&self, link: &Link, index: usize) -> Result<Vec<EntityRow>, String> {
        let (_, entities) = self.rule_as(index, "entities", |kind| match kind {
            RuleKind::Entities(found) => Some(found),
            _ => None,
        })?;
        let cx = Cx { link, rules: self };
        let mut out = Vec::new();
        for (module, at, symbol, subject) in self.entities(link, entities) {
            let env = EntityEnv::of(cx, module.zip(at), symbol.as_deref(), Some(&subject));
            let module_name = module.map(|module| cx.text(module).to_string());
            let selected = class_truth(&entities.selection, &env, self.touch);
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
                    let verdict = match &entities.require {
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

    /// `LevelEvaluator.groups`: the text of level `index`'s group field on every graph module
    /// where it is known and the level's `select` is true, sorted.
    pub fn level_groups(&self, link: &Link, index: usize) -> Result<Vec<String>, String> {
        let level = self
            .levels
            .get(index)
            .ok_or_else(|| format!("no level {index} in the plan"))?;
        let (field, select) = (level.field, level.select.as_ref());
        let cx = Cx { link, rules: self };
        let mut out: Vec<String> = Vec::new();
        for (at, module) in self.modules.iter().enumerate() {
            let env = EntityEnv::of(cx, Some((*module, at as u32)), None, None);
            let group = match env.resolve(field) {
                Value::Text(text) => text.into_owned(),
                Value::Zone(zone) => zone.name.clone(),
                Value::Bool(flag) => if flag { "True" } else { "False" }.to_string(),
                Value::Unknown | Value::None => continue,
                other => return Err(format!("a level groups by a field read as {other:?}")),
            };
            if let Some(node) = select
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

    /// The roots `within` names among the graph's module paths, sorted.
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
