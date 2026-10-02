import assert from 'node:assert/strict';
import test from 'node:test';
import {selectedCustomColumns,customFieldsForSave} from '../src/lib/customColumnDisplay.ts';
const definitions=[{id:12,name:'Difficulty',datatype:'int'},{id:13,name:'Score',datatype:'float'}];
test('guest display choices and personal labels follow the browser across compatible views',()=>{
 const values=new Map([['cwng:catalog-custom-fields-v1','[12]'],['cwng:catalog-custom-field-labels-v1','{"12":"My difficulty"}']]);
 Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:(key:string)=>values.get(key)??null}});
 try {assert.deepEqual(selectedCustomColumns(definitions as any,null),[{id:12,name:'My difficulty',datatype:'int'}]);
 assert.deepEqual(selectedCustomColumns(definitions as any,{role:{anonymous:false},catalog:{custom_field_ids:[13],custom_field_labels:{'13':'My score'}}} as any),[{id:13,name:'My score',datatype:'float'}]);
 } finally {delete (globalThis as any).localStorage;}
});

test('saved stale IDs and labels are pruned to the current server-owned fields',()=>{
 assert.deepEqual(customFieldsForSave([definitions[1]] as any,[12,13],{'12':'Old label','13':'My score'}),{custom_column_ids:[13],custom_column_labels:{'13':'My score'}});
});
