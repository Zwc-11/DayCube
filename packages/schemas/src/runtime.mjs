import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import contracts from './contracts.json' with { type: 'json' };

const ajv = new Ajv2020({ allErrors: true, strict: false });
addFormats(ajv);
const validators = new Map(Object.entries(contracts).map(([name, contract]) => [name, ajv.compile(contract)]));

export function validateContract(name, value) {
  const validate = validators.get(name);
  if (!validate) throw new Error(`Unknown contract: ${name}`);
  if (!validate(value)) throw new Error(`${name}: ${ajv.errorsText(validate.errors)}`);
  return value;
}
