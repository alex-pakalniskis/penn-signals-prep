
import pandas as pd
import os
import numpy as np
import matplotlib.pyplot as plt
import re
import copy

pd.options.display.max_rows = 4000
pd.options.display.max_columns = 4000

# define relative paths
assert os.getcwd().split("/")[-1] == "chime_sims"
datadir = f"{os.getcwd()}/data/"
outdir = f"{os.getcwd()}/output/"
figdir = f"{os.getcwd()}/figures/"

# import parameters
params = pd.read_csv(f"{datadir}parameters.csv")


def write_txt(str, path):
    text_file = open(path, "w")
    text_file.write(str)
    text_file.close()

# pull parameters, potentially as a draw
def getparm(x, p_df, random_draw=False):
    if random_draw is False:
        return float(p_df.base.loc[p_df.param == x])
    else:
        distro = p_df.distribution.loc[p_df.param == x].iloc[0]
        if distro != "constant":
            p = tuple(p_df[['p1', 'p2']].loc[p_df.param == x].iloc[0])
            draw = getattr(np.random, distro)(*p)
            return draw
        else:
            return float(p_df.base.loc[p_df.param == x])


# SIR simulation
def sir(y, beta, gamma, N):
    S, I, R = y
    Sn = (-beta * S * I) + S
    In = (beta * S * I - gamma * I) + I
    Rn = gamma * I + R
    if Sn < 0:
        Sn = 0
    if In < 0:
        In = 0
    if Rn < 0:
        Rn = 0
    scale = N / (Sn + In + Rn)
    return Sn * scale, In * scale, Rn * scale


# Run the SIR model forward in time
def sim_sir(S, I, R, beta, gamma, n_days):
    N = S + I + R
    s, i, r = [S], [I], [R]
    for day in range(n_days):
        y = S, I, R
        S, I, R = sir(y, beta, gamma, N)
        s.append(S)
        i.append(I)
        r.append(R)

    s, i, r = np.array(s), np.array(i), np.array(r)
    return s, i, r


def sensitivity_wrapper(seed=8675309, random_draw=True, modparm=None, 
                        modval=None, output_SIR = False):
    np.random.seed(seed)
    # define a temporary parameter matrix in which the twiddled parameter is set as a constant
    p_df = copy.deepcopy(params)
    if modparm is not None:
        if type(modparm).__name__ == "str":
            assert modparm in p_df.param.tolist()
            p_df.loc[p_df['param'] == modparm, 'base'] = float(modval)
            p_df.loc[p_df['param'] == modparm, 'distribution'] = 'constant'
        elif type(modparm).__name__ == "list":
            assert(all([i in p_df.param.tolist() for i in modparm]))
            for i in range(len(modparm)):
                p_df.loc[p_df['param'] == modparm[i], 'base'] = float(modval[i])
                p_df.loc[p_df['param'] == modparm[i], 'distribution'] = 'constant'
    # define all of the parameters via calls to getparm
    recovery_days = getparm("recovery_days", p_df=p_df, random_draw = random_draw)
    doubling_time = getparm("doubling_time", random_draw=random_draw, p_df=p_df)
    soc_dist = getparm('soc_dist', random_draw=random_draw, p_df=p_df)
    hosp_prop = getparm('hosp_prop', random_draw=random_draw, p_df=p_df)
    ICU_prop = getparm('ICU_prop', random_draw=random_draw, p_df=p_df)
    vent_prop = getparm('vent_prop', random_draw=random_draw, p_df=p_df)
    hosp_LOS = getparm('hosp_LOS', random_draw=random_draw, p_df=p_df)
    ICU_LOS = getparm('ICU_LOS', random_draw=random_draw, p_df=p_df)
    vent_LOS = getparm('vent_LOS', random_draw=random_draw, p_df=p_df)
    #
    gamma = 1 / recovery_days  # , random_draw=random_draw)
    doubling_time = doubling_time
    intrinsic_growth_rate = 2 ** (1 / doubling_time) - 1
    total_infections = getparm('n_hosp', p_df=p_df) / \
                       getparm('mkt_share', p_df=p_df) / \
                       hosp_prop
    detection_prob = getparm('n_infec', p_df=p_df) / total_infections
    beta = (
                   intrinsic_growth_rate + gamma
           ) / getparm('region_pop', p_df=p_df) * (1 - soc_dist)
    n_days = 200

    s, i, r = sim_sir(S=getparm('region_pop', p_df=p_df),
                      I=getparm('n_infec', p_df=p_df) / detection_prob,
                      R=0,
                      beta=beta,
                      gamma=gamma,
                      n_days=n_days)
    if output_SIR == True:
        return np.vstack([s,i,r]).T
    hosp_raw = hosp_prop
    ICU_raw = hosp_raw * ICU_prop  # coef param
    vent_raw = ICU_raw * vent_prop  # coef param
    
    ds = np.diff(s*-1)
    ds = np.array([0]+list(ds))

    hosp = ds * hosp_raw * getparm('mkt_share', p_df=p_df)
    icu = ds * ICU_raw * getparm('mkt_share', p_df=p_df)
    vent = ds * vent_raw * getparm('mkt_share', p_df=p_df)

    # make a data frame with all the stats for plotting
    days = np.array(range(0, n_days + 1))
    data_list = [days, hosp, icu, vent]
    data_dict = dict(zip(["day", "hosp_adm", "icu_adm", "vent_adm"], data_list))
    projection = pd.DataFrame.from_dict(data_dict)
    projection_admits = projection
    projection_admits["day"] = range(projection_admits.shape[0])
    # census df
    hosp_LOS_raw = hosp_LOS
    ICU_LOS_raw = ICU_LOS
    vent_LOS_raw = ICU_LOS_raw * vent_LOS  # this is a coef

    los_dict = {
        "hosp_census": hosp_LOS_raw,
        "icu_census": ICU_LOS_raw,
        "vent_census": vent_LOS_raw,
    }
    census_dict = {}
    for k, los in los_dict.items():
        census = (
                projection_admits.cumsum().iloc[:-int(los), :]
                - projection_admits.cumsum().shift(int(los)).fillna(0)
        ).apply(np.ceil)
        census_dict[k] = census[re.sub("_census", "_adm", k)]
    proj = pd.concat([projection_admits, pd.DataFrame(census_dict)], axis=1)
    proj = proj.fillna(0)
    
    if random_draw is True:
        output = dict(days=np.asarray(proj.day),
                      arr=np.asarray(proj)[:, 1:],
                      names=proj.columns.tolist()[1:],
                      parms = dict(doubling_time = doubling_time,
                                   soc_dist = soc_dist,
                                   hosp_prop = hosp_prop,
                                   ICU_prop = ICU_prop,
                                   vent_prop = vent_prop,
                                   hosp_LOS = hosp_LOS,
                                   ICU_LOS = ICU_LOS,
                                   vent_LOS = vent_LOS,
                                   recovery_days = recovery_days))
        return output
    else:
        return proj



