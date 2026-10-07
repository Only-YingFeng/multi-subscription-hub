// Local simulation only. Configuration input and output stay in the private directory.
const fs = require('fs');
const vm = require('vm');
const [engine, input, metadata, output] = process.argv.slice(2);
try {
  const context = vm.createContext({});
  vm.runInContext(fs.readFileSync(engine, 'utf8'), context, {timeout: 4000});
  context.config = JSON.parse(fs.readFileSync(input, 'utf8'));
  context.metadata = JSON.parse(fs.readFileSync(metadata, 'utf8'));
  const result = vm.runInContext('czoTransform(config, metadata)', context, {timeout: 4000});
  fs.writeFileSync(output, JSON.stringify(result));
} catch (e) {
  process.stderr.write('CZO_TRANSFORM_FAILED:' + e.name + '\n');
  process.exit(1);
}
