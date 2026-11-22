export const releaseViewTemplate = `
<section class="panel">
  <h2>解笼拨付</h2>
  <p class="hint">
    仅核验通过的笼可拨付。默认把可用额全额拨给受益人，也可部分解笼。
    <strong>甲路径</strong>：拨付入册则账户冻结必同步扣减；扣冻不成则拨付册零增，
    记「出金扣冻偏航」并拒写。同笼双人并发出金须抢笼锁，后到只记争用。
  </p>
  <div class="grid-2">
    <div>
      <div class="form-row">
        <div>
          <label>通过态笼</label>
          <select v-model.number="releaseForm.cage_id">
            <option :value="0">请选择</option>
            <option v-for="c in passCages" :key="c.id" :value="c.id">
              {{ c.cage_code }} · 可用 {{ fmt(c.available) }}
            </option>
          </select>
        </div>
        <div>
          <label>金额（空=全额）</label>
          <input type="number" v-model.number="releaseForm.amount" min="0" step="1000" placeholder="全额"/>
        </div>
        <div>
          <label>收款方（空=受益人）</label>
          <input v-model="releaseForm.payee"/>
        </div>
      </div>
      <div class="actions">
        <button class="ok" @click="doRelease">解笼拨付</button>
      </div>
      <div class="fate-box" v-if="releaseFate">
        <div>路径：{{ releaseFate.path }} · 锁定 {{ releaseFate.locked_on }}</div>
        <div>{{ releaseFate.doctrine }}</div>
        <div v-if="releaseFate.last">
          最近一笔 {{ releaseFate.last.release_ref }}：拨付 {{ fmt(releaseFate.last.amount) }} ·
          冻结同步扣减 {{ fmt(releaseFate.last.frozen_deducted) }} ·
          扣后冻结 {{ fmt(releaseFate.last.account_frozen_after) }}
        </div>
        <div v-else>尚无拨付，冻结未动。</div>
      </div>
      <div class="deviation-box" v-if="releaseDeviations.length">
        <h3>出金扣冻偏航 {{ releaseDeviations.length }} 条（拒写）</h3>
        <ul>
          <li v-for="d in releaseDeviations" :key="d.id">
            {{ d.created_at }} · {{ d.target }} · {{ d.payload.reason }}
          </li>
        </ul>
      </div>
    </div>
    <div>
      <table>
        <thead>
          <tr>
            <th>凭证</th><th>笼</th><th>金额</th><th>冻结扣减</th>
            <th>扣后冻结</th><th>收款方</th><th>笼代</th><th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in releases" :key="r.id">
            <td>{{ r.release_ref }}</td>
            <td>{{ r.cage_code }}</td>
            <td>{{ fmt(r.amount) }}</td>
            <td>
              <span class="badge" :class="releaseFreezeClass(r)">{{ fmt(r.frozen_deducted) }}</span>
            </td>
            <td>{{ fmt(r.account_frozen_after) }}</td>
            <td>{{ r.payee }}</td>
            <td>{{ r.cage_generation }}</td>
            <td>{{ r.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</section>
`;
