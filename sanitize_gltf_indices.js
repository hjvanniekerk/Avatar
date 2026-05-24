const fs = require("fs");
const path = require("path");

const COMPONENT_BYTE_SIZE = {5121: 1, 5123: 2, 5125: 4};

function align4(value){
  return (value + 3) & ~3;
}

function readGlb(file){
  const data = fs.readFileSync(file);
  if (data.toString("ascii", 0, 4) !== "glTF") throw new Error("Not a GLB: " + file);
  let offset = 12;
  let json = null;
  let bin = null;
  while (offset < data.length){
    const length = data.readUInt32LE(offset);
    const type = data.toString("ascii", offset + 4, offset + 8);
    const body = data.subarray(offset + 8, offset + 8 + length);
    if (type === "JSON") json = JSON.parse(body.toString("utf8").replace(/\0+$/g, "").trimEnd());
    if (type === "BIN\0") bin = Buffer.from(body);
    offset += 8 + length;
  }
  if (!json || !bin) throw new Error("GLB must contain JSON and BIN chunks");
  return {json, bin};
}

function componentArray(componentType, buffer, byteOffset, count){
  if (componentType === 5121) return new Uint8Array(buffer.buffer, buffer.byteOffset + byteOffset, count);
  if (componentType === 5123) return new Uint16Array(buffer.buffer, buffer.byteOffset + byteOffset, count);
  if (componentType === 5125) return new Uint32Array(buffer.buffer, buffer.byteOffset + byteOffset, count);
  throw new Error("Unsupported index component type " + componentType);
}

function accessorByteOffset(json, accessor){
  const view = json.bufferViews[accessor.bufferView];
  return (view.byteOffset || 0) + (accessor.byteOffset || 0);
}

function encodeIndices(indices, componentType){
  const byteSize = COMPONENT_BYTE_SIZE[componentType];
  const out = Buffer.alloc(indices.length * byteSize);
  indices.forEach((value, i) => {
    if (componentType === 5121) out.writeUInt8(value, i);
    else if (componentType === 5123) out.writeUInt16LE(value, i * 2);
    else if (componentType === 5125) out.writeUInt32LE(value, i * 4);
  });
  return out;
}

function appendBufferView(json, binParts, buffer){
  const currentLength = binParts.reduce((sum, part) => sum + part.length, 0);
  const aligned = align4(currentLength);
  if (aligned > currentLength) binParts.push(Buffer.alloc(aligned - currentLength));
  const byteOffset = aligned;
  binParts.push(buffer);
  const viewIndex = json.bufferViews.length;
  json.bufferViews.push({
    buffer: 0,
    byteOffset,
    byteLength: buffer.length,
    target: 34963,
  });
  return viewIndex;
}

function sanitize(input, output){
  const {json, bin} = readGlb(input);
  const binParts = [bin];
  let primitivesChecked = 0;
  let primitivesChanged = 0;
  let primitivesDropped = 0;
  let trianglesDropped = 0;

  for (const mesh of json.meshes || []){
    for (const primitive of mesh.primitives || []){
      const material = primitive.material == null ? null : json.materials && json.materials[primitive.material];
      const baseColor = material && material.pbrMetallicRoughness && material.pbrMetallicRoughness.baseColorFactor;
      if ((material && material.name === "M_Hide") || (Array.isArray(baseColor) && baseColor[3] === 0 && material && material.alphaMode === "MASK")){
        primitive.__drop = true;
        primitivesDropped += 1;
        continue;
      }
      if (primitive.indices == null || !primitive.attributes || primitive.attributes.POSITION == null) continue;
      const mode = primitive.mode == null ? 4 : primitive.mode;
      if (mode !== 4) continue;
      primitivesChecked += 1;
      const indexAccessor = json.accessors[primitive.indices];
      const positionAccessor = json.accessors[primitive.attributes.POSITION];
      const vertexCount = Number(positionAccessor.count || 0);
      const indexOffset = accessorByteOffset(json, indexAccessor);
      const values = componentArray(indexAccessor.componentType, bin, indexOffset, indexAccessor.count);
      const cleaned = [];
      for (let i = 0; i + 2 < values.length; i += 3){
        const a = Number(values[i]);
        const b = Number(values[i + 1]);
        const c = Number(values[i + 2]);
        if (a >= vertexCount || b >= vertexCount || c >= vertexCount){
          trianglesDropped += 1;
          continue;
        }
        cleaned.push(a, b, c);
      }
      if (cleaned.length !== values.length){
        primitivesChanged += 1;
        const componentType = vertexCount <= 255 ? 5121 : (vertexCount <= 65535 ? 5123 : 5125);
        const indexBuffer = encodeIndices(cleaned, componentType);
        const bufferView = appendBufferView(json, binParts, indexBuffer);
        let min = cleaned.length ? cleaned[0] : 0;
        let max = cleaned.length ? cleaned[0] : 0;
        for (const index of cleaned){
          if (index < min) min = index;
          if (index > max) max = index;
        }
        indexAccessor.bufferView = bufferView;
        delete indexAccessor.byteOffset;
        indexAccessor.componentType = componentType;
        indexAccessor.count = cleaned.length;
        indexAccessor.min = [min];
        indexAccessor.max = [max];
      }
    }
    mesh.primitives = (mesh.primitives || []).filter(primitive => !primitive.__drop);
    mesh.primitives.forEach(primitive => { delete primitive.__drop; });
  }

  const finalBinLength = align4(binParts.reduce((sum, part) => sum + part.length, 0));
  const finalBin = Buffer.concat([...binParts, Buffer.alloc(finalBinLength - binParts.reduce((sum, part) => sum + part.length, 0))]);
  json.buffers[0].byteLength = finalBin.length;
  json.asset = json.asset || {version: "2.0"};
  json.asset.extras = Object.assign({}, json.asset.extras || {}, {
    index_sanitizer: "dropped out-of-range Unreal GLTF triangles",
    primitives_checked: primitivesChecked,
    primitives_changed: primitivesChanged,
    primitives_dropped: primitivesDropped,
    triangles_dropped: trianglesDropped,
  });

  let jsonBuffer = Buffer.from(JSON.stringify(json), "utf8");
  jsonBuffer = Buffer.concat([jsonBuffer, Buffer.alloc(align4(jsonBuffer.length) - jsonBuffer.length, 0x20)]);

  const totalLength = 12 + 8 + jsonBuffer.length + 8 + finalBin.length;
  const header = Buffer.alloc(12);
  header.write("glTF", 0, "ascii");
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(totalLength, 8);
  const jsonHeader = Buffer.alloc(8);
  jsonHeader.writeUInt32LE(jsonBuffer.length, 0);
  jsonHeader.write("JSON", 4, "ascii");
  const binHeader = Buffer.alloc(8);
  binHeader.writeUInt32LE(finalBin.length, 0);
  binHeader.write("BIN\0", 4, "ascii");
  fs.writeFileSync(output, Buffer.concat([header, jsonHeader, jsonBuffer, binHeader, finalBin]));

  return {input, output, primitivesChecked, primitivesChanged, primitivesDropped, trianglesDropped, bytes: fs.statSync(output).size};
}

const input = process.argv[2] || path.resolve("assets", "metahuman-static-test.glb");
const output = process.argv[3] || path.resolve("assets", "metahuman-static-clean.glb");
console.log(JSON.stringify(sanitize(input, output), null, 2));
