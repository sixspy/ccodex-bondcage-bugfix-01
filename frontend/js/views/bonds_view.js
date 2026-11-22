export const bondsViewTemplate = `
<section class="panel">
  <h2>保证金户</h2>
  <p class="hint">
    开户入金后，可用额 = 余额 − 已冻结。入笼只动冻结，不解出系统。
    解笼拨付时<strong>冻结扣减</strong>须与拨付同额；下方「冻结互证」按真实账户冻结
    与<strong>活笼可用</strong>合计比对，不恒绿。
  </p>
  <div class="grid-2">
    <div>
      <h3>开户</h3>
      <div class="form-row">
        <div>
          <label>当事人键</label>
          <input v-model="bondForm.party_key" placeholder="P-NEW"/>
        </div>
        <div>
          <label>名称</label>
          <input v-model="bondForm.party_name" placeholder="新公司"/>
        </div>
        <div>
          <label>开户入金</label>
          <input type="number" v-model.number="bondForm.initial_deposit" min="0" step="1000"/>
        </div>
      </div>
      <div class="actions">
        <button @click="openBond">开户</button>
      </div>
      <h3>入金</h3>
      <div class="form-row">
        <div>
          <label>当事人键</label>
          <input v-model="depositForm.party_key" placeholder="P-WEST"/>
        </div>
        <div>
          <label>金额</label>
          <input type="number" v-model.number="depositForm.amount" min="1" step="1000"/>
        </div>
        <div>
          <label>备注</label>
          <input v-model="depositForm.memo"/>
        </div>
      </div>
      <div class="actions">
        <button class="ok" @click="deposit">入金</button>
        <button class="secondary" @click="loadBondDetail(depositForm.party_key)">查看明细</button>
      </div>
    </div>
    <div>
      <h3>账户一览</h3>
      <table>
        <thead>
          <tr><th>键</th><th>名称</th><th>余额</th><th>冻结</th><th>可用</th><th>活笼可用</th><th>冻结互证</th><th>状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="b in bonds" :key="b.id" @click="loadBondDetail(b.party_key)" style="cursor:pointer">
            <td>{{ b.party_key }}</td>
            <td>{{ b.party_name }}</td>
            <td>{{ fmt(b.balance) }}</td>
            <td>{{ fmt(b.frozen) }}</td>
            <td>{{ fmt(b.balance - b.frozen) }}</td>
            <td>{{ fmt(b.live_cage_available) }}</td>
            <td>
              <span class="badge" :class="b.frozen_matches_live?'pass':'fail'">
                {{ b.frozen_matches_live ? '一致' : '失衡' }}
              </span>
            </td>
            <td><span class="badge frozen">{{ b.status }}</span></td>
          </tr>
        </tbody>
      </table>
      <div v-if="bondDetail" style="margin-top:12px">
        <h3>明细 · {{ bondDetail.account.party_key }}</h3>
        <p class="hint">入金流水 {{ bondDetail.deposits.length }} 条 · 关联笼 {{ bondDetail.cages.length }} 只</p>
        <table>
          <thead><tr><th>入金</th><th>备注</th><th>时间</th><th>操作者</th></tr></thead>
          <tbody>
            <tr v-for="d in bondDetail.deposits" :key="d.id">
              <td>{{ fmt(d.amount) }}</td>
              <td>{{ d.memo }}</td>
              <td>{{ d.created_at }}</td>
              <td>{{ d.actor }}</td>
            </tr>
          </tbody>
        </table>
        <div class="proof-box">
          冻结互证：账户冻结 {{ fmt(bondDetail.account.frozen) }}
          · 活笼可用 {{ fmt(bondDetail.account.live_cage_available) }}
          · {{ bondDetail.account.frozen_matches_live ? '一致' : '失衡' }}
        </div>
        <h3 style="margin-top:12px">解笼扣冻留痕</h3>
        <table>
          <thead><tr><th>凭证</th><th>笼</th><th>拨付</th><th>冻结扣减</th><th>扣后冻结</th><th>时间</th></tr></thead>
          <tbody>
            <tr v-for="r in bondDetail.release_freeze" :key="r.id">
              <td>{{ r.release_ref }}</td>
              <td>{{ r.cage_code }}</td>
              <td>{{ fmt(r.amount) }}</td>
              <td>
                <span class="badge" :class="r.frozen_deducted === r.amount ? 'pass' : 'fail'">
                  {{ fmt(r.frozen_deducted) }}
                </span>
              </td>
              <td>{{ fmt(r.account_frozen_after) }}</td>
              <td>{{ r.created_at }}</td>
            </tr>
            <tr v-if="!(bondDetail.release_freeze||[]).length">
              <td colspan="6">尚无解笼拨付，冻结未动。</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</section>
`;
