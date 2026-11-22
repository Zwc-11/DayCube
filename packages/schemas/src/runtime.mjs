// Validation of the canonical contracts (*.schema.json) in the browser and in Node. The validators
// are compiled ahead of time (validators.mjs, from scripts/generate-contracts.mjs) with the same
// ajv and options the contracts were checked with before, so nothing is compiled at page load.
import * as validators from './validators.mjs';

/** ajv's errorsText: `data<path> <message>`, joined by ", ". */
function errorsText(errors) {
  return (errors ?? []).map(e => `data${e.instancePath} ${e.message}`).join(', ');
}

export function validateContract(name, value) {
  const validate = Object.hasOwn(validators, name) ? validators[name] : undefined;
  if (!validate) throw new Error(`Unknown contract: ${name}`);
  if (!validate(value)) throw new Error(`${name}: ${errorsText(validate.errors)}`);
  return value;
}
