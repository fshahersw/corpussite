import assert from 'node:assert/strict';
import test from 'node:test';
import {handleNavigation} from './navigation-api.mjs';

const state={name:'Michigan',abbr:'MI',families:{statutes:{official_capture:{total:1,body:0},imported_collection:2,third_party_snapshot:40658,pending_publication:3},court_rules:{official_capture:{total:4},imported_collection:5,third_party_snapshot:1025,pending_publication:6}},open_us_law_rule_sets:{civil_procedure:103},open_us_law_other_rows:{guidance:145},topic_counts:{sol:137},reviewed_labels:{by_class:{hub_or_index:1}},county_layer:{counties_total:83},gaps:['statewide_forms_none'],qualification:'Counts are saved rows.'};
const matrix={available:true,rows:[state],totals:{jurisdictions:1,by_family:{statutes:{official_capture:1,imported_collection:2,third_party_snapshot:40658,pending_publication:3}},open_us_law_court_rule_rows_typed:36086},gaps:{statewide_forms_none:['MI']},definitions:{third_party_snapshot:'Open US Law exported rows.',official_capture:'Official saved pages'},qualification:'Counts are saved rows.'};
function context(values={},ready=false){return {async context(key){return values[key]??null;},async dataset(){return {ready};},async query(){throw new Error('An unpublished endpoint must not query rows');}};}

test('matrix excludes unpublished publisher counts while preserving exact inventory and core coverage',async()=>{
 const before=structuredClone(matrix),ctx=context({'coverage:matrix':matrix});
 const data=await handleNavigation('/api/coverage/matrix',{},ctx);
 assert.equal(data.rows[0].families.statutes.third_party_snapshot,0);
 assert.equal(data.totals.by_family.statutes.third_party_snapshot,0);
 assert.equal(data.totals.open_us_law_court_rule_rows_typed,0);
 assert.equal(data.pending_inventory.open_us_law.by_family.statutes,40658);
 assert.equal(data.pending_inventory.open_us_law.open_us_law_court_rule_rows_typed,36086);
 assert.equal(data.rows[0].pending_inventory.open_us_law.available,false);
 assert.deepEqual(data.rows[0].families.statutes,{...state.families.statutes,third_party_snapshot:0});
 assert.deepEqual(data.rows[0].county_layer,state.county_layer);
 assert.deepEqual(data.rows[0].reviewed_labels,state.reviewed_labels);
 assert.deepEqual(data.gaps.statewide_forms_none,['MI']);
 assert.deepEqual(data.gaps.open_us_law_not_published,['MI']);
 assert.match(data.qualification,/historical saved-inventory basis/);
 assert.deepEqual(matrix,before,'Frozen context must not be mutated');
});
test('state projection separates unpublished rule sets, other rows and topic inventory without altering core facts',async()=>{
 const before=structuredClone(state),ctx=context({'state:aliases':{mi:'MI',michigan:'MI'},'coverage:state:MI':state});
 const data=await handleNavigation('/api/coverage/state',{state:'Michigan'},ctx);
 for(const field of ['open_us_law_rule_sets','open_us_law_other_rows']){assert.deepEqual(data[field],{});assert.deepEqual(data.pending_inventory.open_us_law[field],state[field]);}
 assert.deepEqual(data.topic_counts,{});assert.deepEqual(data.pending_inventory.topic_candidates.topic_counts,state.topic_counts);
 assert.equal(data.publication.open_us_law_available,false);
 assert.equal(data.families.court_rules.imported_collection,5);
 assert.equal(data.families.court_rules.pending_publication,6);
 assert.equal(data.families.court_rules.official_capture.total,4);
 assert.deepEqual(state,before);
});
test('published Open US Law leaves native matrix and state payloads exactly unchanged',async()=>{
 const ctx=context({'coverage:matrix':matrix,'state:aliases':{mi:'MI'},'coverage:state:MI':state},true);
 assert.equal(await handleNavigation('/api/coverage/matrix',{},ctx),matrix);
 assert.equal(await handleNavigation('/api/coverage/state',{state:'MI'},ctx),state);
});
test('known held state and topic/label endpoints report publication pending; invalid states remain 404',async()=>{
 for(const [path,params,values] of [['/api/coverage/state',{state:'MI'},{}],['/api/coverage/state',{state:'MI'},{'state:aliases':{mi:'MI'}}],['/api/coverage/topics',{topic:'sol'},{}],['/api/coverage/labels',{},{}]]){
  const response=await handleNavigation(path,params,context(values));assert.equal(response.status,503);assert.equal((await response.json()).code,'publication_pending');
 }
 const unknown=await handleNavigation('/api/coverage/state',{state:'INVALID'},context({'state:aliases':{mi:'MI'}}));assert.equal(unknown.status,404);
});
